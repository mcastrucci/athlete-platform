"""Extraction enqueue and worker handler on PostgreSQL, with a fake extractor and synthetic text."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import Barrier
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from athlete_platform.application.extraction import (
    ExtractionOutput,
    ExtractionRequest,
    ExtractionUsage,
    InvalidExtractionOutput,
    TransientExtractionError,
    extraction_job_key,
)
from athlete_platform.application.jobs import FailureCode, JobState, QueueLimits
from athlete_platform.application.profiles import ProfileContent
from athlete_platform.application.sources import (
    DuplicateDecision,
    InterpretationContent,
    SourceSubmission,
    SourceView,
)
from athlete_platform.domain.identity import VerifiedIdentity
from athlete_platform.infrastructure.database import build_session_factory, transaction
from athlete_platform.infrastructure.job_models import ExtractionJob
from athlete_platform.infrastructure.jobs import PostgresJobs
from athlete_platform.infrastructure.profiles import PostgresProfiles
from athlete_platform.infrastructure.source_models import InterpretationRevision
from athlete_platform.infrastructure.sources import PostgresSources
from athlete_platform.worker.extraction import extraction_handler
from athlete_platform.worker.runner import TransientJobError, run_worker
from tests.integration.test_jobs import expire, make_due
from tests.integration.test_sources import athlete, auth, run_client

pytestmark = pytest.mark.integration

TEXT = "Monday: synthetic back squat 4x5 @ 100 kg"
OTHER_TEXT = "Tuesday: synthetic row 5 x 500 m"
CONTENT = InterpretationContent.model_validate(
    {
        "sessions": [
            {
                "status": "performed",
                "date_text": "Monday",
                "items": [{"exercise_text": "back squat", "kind": "strength", "sets": 4}],
            }
        ]
    }
)


@pytest.fixture(autouse=True)
def isolated_queue(database_url: str, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATHLETE_MIGRATION_DATABASE_URL", database_url)
    command.upgrade(Config("alembic.ini"), "head")
    # Leftover jobs from other modules would otherwise be claimed by these workers.
    with transaction(build_session_factory(engine)) as session:
        session.execute(delete(ExtractionJob))


class FakeExtractor:
    """Plays a script of outcomes: ok, transient, invalid, or schema (a pydantic error)."""

    def __init__(self, *script: str, during_call: Callable[[], None] | None = None) -> None:
        self.script = list(script)
        self.requests: list[ExtractionRequest] = []
        self.during_call = during_call

    def extract(self, request: ExtractionRequest) -> ExtractionOutput:
        self.requests.append(request)
        if self.during_call is not None:
            self.during_call()
        step = self.script.pop(0) if self.script else "ok"
        if step == "transient":
            raise TransientExtractionError
        if step == "invalid":
            raise InvalidExtractionOutput
        if step == "schema":
            InterpretationContent.model_validate({"sessions": "not a list"})
        if step == "unvalidated":
            return ExtractionOutput(
                content=InterpretationContent.model_construct(sessions="not a list"),
                usage=usage(),
                prompt_version="synthetic-1",
            )
        return ExtractionOutput(content=CONTENT, usage=usage(), prompt_version="synthetic-1")


def usage() -> ExtractionUsage:
    return ExtractionUsage(provider="fake", model="fake-1", input_tokens=10, output_tokens=5)


class World:
    def __init__(self, engine: Engine, limits: QueueLimits | None = None) -> None:
        self.engine = engine
        self.factory = build_session_factory(engine)
        self.identity = VerifiedIdentity("https://synthetic.invalid", str(uuid4()))
        profile = PostgresProfiles(self.factory, self.identity).create(
            ProfileContent(timezone="Europe/Lisbon")
        )
        self.athlete_id = profile.athlete_id
        self.jobs = PostgresJobs(self.factory, limits)
        self.sources = PostgresSources(self.factory, self.identity, self.jobs)

    def submit(self, key: str, text: str = TEXT) -> tuple[SourceView, bool]:
        return self.sources.submit(
            self.athlete_id, SourceSubmission(idempotency_key=key, text=text)
        )

    def job_ids(self, source_id: UUID | None = None) -> list[UUID]:
        query = select(ExtractionJob.id).where(ExtractionJob.athlete_id == self.athlete_id)
        if source_id is not None:
            query = query.where(ExtractionJob.idempotency_key == extraction_job_key(source_id))
        with transaction(self.factory) as session:
            return list(session.scalars(query))

    def revisions(self, source_id: UUID) -> list[InterpretationRevision]:
        with transaction(self.factory) as session:
            rows = session.scalars(
                select(InterpretationRevision).where(InterpretationRevision.source_id == source_id)
            ).all()
            session.expunge_all()
            return list(rows)

    def run(self, extractor: FakeExtractor) -> int:
        return run_worker(self.jobs, extraction_handler(self.factory, extractor))

    def job(self, source_id: UUID) -> Any:
        (job_id,) = self.job_ids(source_id)
        return self.jobs.get(self.identity, job_id)

    def decide(self, source_id: UUID, decision: Literal["keep", "discard"]) -> SourceView:
        return self.sources.decide_duplicate(
            self.athlete_id,
            source_id,
            DuplicateDecision(decision=decision),
        )


def test_submit_enqueues_one_job_and_replays_return_it(engine: Engine) -> None:
    world = World(engine)
    source, created = world.submit("one")
    assert created and source.extraction is not None
    assert source.extraction.state == "queued" and source.extraction.error_code is None
    (job_id,) = world.job_ids()
    assert source.extraction.job_id == job_id
    with transaction(world.factory) as session:
        stored = session.get(ExtractionJob, job_id)
        assert stored is not None
        assert stored.idempotency_key == f"extract-source:{source.id}"
        assert stored.payload == {"source_id": str(source.id)}
    replay, created = world.submit("one")
    assert not created and replay.extraction == source.extraction
    assert world.job_ids() == [job_id]
    assert world.sources.read(world.athlete_id, source.id).extraction == source.extraction


def test_lost_enqueue_heals_on_replay(engine: Engine) -> None:
    world = World(engine)
    source, _ = world.submit("one")
    with transaction(world.factory) as session:
        session.execute(delete(ExtractionJob))
    assert world.sources.read(world.athlete_id, source.id).extraction is None
    replay, created = world.submit("one")
    assert not created and replay.extraction is not None and replay.extraction.state == "queued"
    assert len(world.job_ids()) == 1


def test_concurrent_identical_submits_create_one_job(engine: Engine) -> None:
    for _ in range(5):
        world = World(engine)
        barrier = Barrier(3)

        def submit(world: World = world, barrier: Barrier = barrier) -> tuple[UUID, UUID | None]:
            barrier.wait(timeout=10)
            source, _ = world.submit("race")
            assert source.extraction is not None
            return source.id, source.extraction.job_id

        with ThreadPoolExecutor(3) as pool:
            outcomes = [pool.submit(submit) for _ in range(3)]
            results = {future.result(timeout=30) for future in outcomes}
        assert len(results) == 1
        assert world.job_ids() == [next(iter(results))[1]]


def test_duplicate_decisions_control_enqueue(engine: Engine) -> None:
    world = World(engine)
    first, _ = world.submit("first")
    pending, _ = world.submit("second")
    assert pending.duplicate_status == "pending" and pending.extraction is None
    assert world.job_ids(pending.id) == []
    assert world.submit("second")[0].extraction is None

    kept = world.decide(pending.id, "keep")
    assert kept.extraction is not None and kept.extraction.state == "queued"
    assert world.decide(pending.id, "keep").extraction == kept.extraction
    assert world.submit("second")[0].extraction == kept.extraction
    assert len(world.job_ids(pending.id)) == 1

    discarded, _ = world.submit("third")
    assert discarded.duplicate_status == "pending"
    after = world.decide(discarded.id, "discard")
    assert after.extraction is None and world.decide(discarded.id, "discard").extraction is None
    assert world.submit("third")[0].extraction is None
    assert world.job_ids(discarded.id) == []
    assert len(world.job_ids()) == 2 and first.extraction is not None


def test_worker_records_first_interpretation_with_provenance(engine: Engine) -> None:
    world = World(engine)
    source, _ = world.submit("one")
    extractor = FakeExtractor()
    assert world.run(extractor) == 1
    assert [(r.source_id, r.text, r.timezone) for r in extractor.requests] == [
        (source.id, TEXT, "Europe/Lisbon")
    ]
    job = world.job(source.id)
    assert job.state == JobState.SUCCEEDED and job.result == {"interpretation_version": 1}
    (revision,) = world.revisions(source.id)
    assert revision.version == 1 and revision.origin == "extraction"
    assert revision.run_id == job.id
    assert revision.provenance == {
        "run_id": str(job.id),
        "provider": "fake",
        "model": "fake-1",
        "prompt_version": "synthetic-1",
        "schema_version": 1,
    }
    view = world.sources.read(world.athlete_id, source.id)
    assert view.interpretation_version == 1
    assert view.extraction is not None and view.extraction.state == "succeeded"
    assert world.run(extractor) == 0


def test_duplicate_delivery_and_reclaim_keep_one_revision(engine: Engine) -> None:
    world = World(engine)
    source, _ = world.submit("one")
    extractor = FakeExtractor()
    handler = extraction_handler(world.factory, extractor)
    first = world.jobs.claim()
    assert first is not None
    assert handler(first, partial(world.jobs.heartbeat, first)) == {"interpretation_version": 1}
    # The worker crashed before completing; the lease expires and the job is delivered again.
    expire(engine, first.job.id)
    second = world.jobs.claim()
    assert second is not None and second.job.attempts == 2
    assert handler(second, partial(world.jobs.heartbeat, second)) == {"interpretation_version": 1}
    assert world.jobs.complete(second, {"interpretation_version": 1})
    assert len(world.revisions(source.id)) == 1
    assert len(extractor.requests) == 1


def test_stale_worker_writes_nothing(engine: Engine) -> None:
    world = World(engine)
    source, _ = world.submit("one")
    extractor = FakeExtractor()
    handler = extraction_handler(world.factory, extractor)
    old = world.jobs.claim()
    assert old is not None
    expire(engine, old.job.id)
    new = world.jobs.claim()
    assert new is not None
    with pytest.raises(TransientJobError):
        handler(old, partial(world.jobs.heartbeat, old))
    assert extractor.requests == [] and world.revisions(source.id) == []

    # Losing the lease during the provider call also prevents the write.
    lost: list[object] = []

    def lose_lease() -> None:
        expire(engine, new.job.id)
        lost.append(world.jobs.claim())

    during = FakeExtractor(during_call=lose_lease)
    with pytest.raises(TransientJobError):
        extraction_handler(world.factory, during)(new, partial(world.jobs.heartbeat, new))
    assert len(during.requests) == 1 and lost[0] is not None
    assert world.revisions(source.id) == []


def test_transient_error_defers_then_succeeds(engine: Engine) -> None:
    world = World(engine)
    source, _ = world.submit("one")
    extractor = FakeExtractor("transient", "ok")
    assert world.run(extractor) == 1
    job = world.job(source.id)
    assert job.state == JobState.DEFERRED and job.error_code == FailureCode.TRANSIENT
    assert world.revisions(source.id) == [] and world.run(extractor) == 0
    make_due(engine, job.id)
    assert world.run(extractor) == 1
    assert world.job(source.id).state == JobState.SUCCEEDED
    assert len(world.revisions(source.id)) == 1


@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")
@pytest.mark.parametrize("kind", ["invalid", "schema", "unvalidated"])
def test_invalid_output_twice_fails_without_a_revision(engine: Engine, kind: str) -> None:
    world = World(engine)
    source, _ = world.submit("one")
    extractor = FakeExtractor(kind, kind)
    assert world.run(extractor) == 1
    job = world.job(source.id)
    assert job.state == JobState.DEFERRED and job.error_code == FailureCode.INVALID_OUTPUT
    make_due(engine, job.id)
    assert world.run(extractor) == 1
    job = world.job(source.id)
    assert job.state == JobState.FAILED and job.error_code == FailureCode.INVALID_OUTPUT
    assert job.attempts == 2 < job.attempt_limit
    assert world.revisions(source.id) == []
    view = world.sources.read(world.athlete_id, source.id)
    assert view.extraction is not None
    assert (view.extraction.state, view.extraction.error_code) == ("failed", "invalid_output")


def test_invalid_once_then_valid_succeeds(engine: Engine) -> None:
    world = World(engine)
    source, _ = world.submit("one")
    extractor = FakeExtractor("invalid", "ok")
    assert world.run(extractor) == 1
    make_due(engine, world.job(source.id).id)
    assert world.run(extractor) == 1
    assert world.job(source.id).state == JobState.SUCCEEDED
    assert len(world.revisions(source.id)) == 1


def test_payload_naming_another_athletes_source_is_terminal(engine: Engine) -> None:
    mine = World(engine)
    theirs = World(engine)
    foreign, _ = theirs.submit("theirs")
    # Remove the other athlete's own job so only the forged one is eligible.
    with transaction(theirs.factory) as session:
        session.execute(delete(ExtractionJob))
    forged = mine.jobs.enqueue(
        mine.identity, mine.athlete_id, "forged", {"source_id": str(foreign.id)}
    )
    missing = mine.jobs.enqueue(
        mine.identity, mine.athlete_id, "missing", {"source_id": str(uuid4())}
    )
    malformed = mine.jobs.enqueue(mine.identity, mine.athlete_id, "bad", {"source_id": 5})
    extractor = FakeExtractor()
    assert mine.run(extractor) == 3
    assert extractor.requests == []
    for job_id in (forged.id, missing.id, malformed.id):
        job = mine.jobs.get(mine.identity, job_id)
        assert job.state == JobState.FAILED and job.error_code == FailureCode.HANDLER_ERROR
        assert job.attempts == 1
    assert theirs.revisions(foreign.id) == []


def test_ineligible_sources_are_terminal_without_a_write(engine: Engine) -> None:
    world = World(engine)
    world.submit("first")
    pending, _ = world.submit("second")
    # A job for a still-pending duplicate must not run, even if one was enqueued by hand.
    job = world.jobs.enqueue(
        world.identity, world.athlete_id, "manual", {"source_id": str(pending.id)}
    )
    world.run(FakeExtractor())
    assert world.jobs.get(world.identity, job.id).state == JobState.FAILED
    assert world.revisions(pending.id) == []


def test_queue_full_keeps_the_source_and_heals_on_replay(engine: Engine) -> None:
    world = World(engine, QueueLimits(active_jobs_per_athlete=1))
    filler = world.jobs.enqueue(world.identity, world.athlete_id, "other-work", {})
    source, created = world.submit("one")
    assert created
    assert source.extraction is not None
    assert source.extraction.model_dump() == {
        "job_id": None,
        "state": "not_queued",
        "error_code": "queue_full",
    }
    stored = world.sources.read(world.athlete_id, source.id)
    assert stored.text == TEXT and stored.extraction is None
    assert world.job_ids(source.id) == []

    lease = world.jobs.claim()
    assert lease is not None and lease.job.id == filler.id
    assert world.jobs.complete(lease, {})
    replay, created = world.submit("one")
    assert not created and replay.id == source.id
    assert replay.extraction is not None and replay.extraction.state == "queued"
    assert len(world.job_ids(source.id)) == 1


def test_http_submission_exposes_extraction_state(database_url: str) -> None:
    async def scenario(client: httpx.AsyncClient) -> None:
        aid = await athlete(client, "alice")
        path = f"/v1/athletes/{aid}/sources"
        body = {"idempotency_key": "http", "text": TEXT}
        created = await client.post(path, json=body, headers=auth("alice"))
        assert created.status_code == 201
        extraction = created.json()["extraction"]
        assert extraction["state"] == "queued" and extraction["error_code"] is None
        replay = await client.post(path, json=body, headers=auth("alice"))
        assert replay.status_code == 200 and replay.json()["extraction"] == extraction
        source_path = f"{path}/{created.json()['id']}"
        read = await client.get(source_path, headers=auth("alice"))
        assert read.json()["extraction"] == extraction
        assert (await client.get(source_path, headers=auth("bob"))).status_code == 404

    run_client(database_url, scenario)
