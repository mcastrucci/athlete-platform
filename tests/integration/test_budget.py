"""Model-spend gate on PostgreSQL, alone and wired into the extraction handler."""

import logging
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, select
from sqlalchemy.engine import Engine

from athlete_platform.application.budget import BudgetExhausted, cost_usd
from athlete_platform.application.extraction import (
    CostEstimate,
    ExtractionConfigurationError,
    ExtractionOutput,
    ExtractionRequest,
    ExtractionUsage,
    InvalidExtractionOutput,
    TransientExtractionError,
)
from athlete_platform.application.jobs import FailureCode, JobState
from athlete_platform.config import Settings
from athlete_platform.infrastructure.anthropic_extractor import AnthropicExtractor
from athlete_platform.infrastructure.budget import PostgresBudget
from athlete_platform.infrastructure.budget_models import BudgetMonth, ModelSpend
from athlete_platform.infrastructure.database import (
    build_engine,
    build_session_factory,
    transaction,
)
from athlete_platform.infrastructure.job_models import ExtractionJob
from athlete_platform.worker.extraction import extraction_handler
from athlete_platform.worker.runner import run_worker
from tests.integration.test_extraction import CONTENT, World
from tests.integration.test_jobs import make_due
from tests.unit.test_anthropic_extractor import FakeClient, message

pytestmark = pytest.mark.integration

MODEL = "claude-haiku-5-5"
NOVEMBER = date(2026, 11, 1)
DECEMBER = date(2026, 12, 1)


def at(year: int, month: int, day: int, hour: int = 12, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, 0, second, tzinfo=UTC)


@pytest.fixture(autouse=True)
def isolated_budget(database_url: str, engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATHLETE_MIGRATION_DATABASE_URL", database_url)
    command.upgrade(Config("alembic.ini"), "head")
    with transaction(build_session_factory(engine)) as session:
        session.execute(delete(ExtractionJob))
        session.execute(delete(ModelSpend))
        session.execute(delete(BudgetMonth))


def gate(engine: Engine, budget: str, now: datetime | None = None) -> PostgresBudget:
    moment = now or at(2026, 11, 10)
    return PostgresBudget(build_session_factory(engine), Decimal(budget), clock=lambda: moment)


def usage(input_tokens: int = 1000, output_tokens: int = 100) -> ExtractionUsage:
    return ExtractionUsage(
        provider="anthropic", model=MODEL, input_tokens=input_tokens, output_tokens=output_tokens
    )


def reserve(subject: PostgresBudget, usd: str, job_id: UUID | None = None) -> UUID:
    job = job_id or uuid4()
    subject.reserve(job, provider="anthropic", model=MODEL, usd=Decimal(usd))
    return job


def test_reservation_within_budget_succeeds_and_exceeding_it_does_not(engine: Engine) -> None:
    subject = gate(engine, "1.00")
    reserve(subject, "0.600000")
    reserve(subject, "0.400000")  # exactly the budget is allowed
    assert subject.month_totals(NOVEMBER) == (Decimal(0), Decimal("1.000000"))
    with pytest.raises(BudgetExhausted):
        reserve(subject, "0.000001")
    assert subject.month_totals(NOVEMBER) == (Decimal(0), Decimal("1.000000"))


def test_concurrent_reservations_never_exceed_the_budget(engine: Engine, database_url: str) -> None:
    big = build_engine(Settings(database_url=SecretStr(database_url), database_pool_size=10))
    try:
        for _ in range(3):
            with transaction(build_session_factory(big)) as session:
                session.execute(delete(ModelSpend))
                session.execute(delete(BudgetMonth))
            subject = gate(big, "1.00")
            barrier = Barrier(10)

            def attempt(subject: PostgresBudget = subject, barrier: Barrier = barrier) -> bool:
                barrier.wait(timeout=10)
                try:
                    reserve(subject, "0.300000")
                except BudgetExhausted:
                    return False
                return True

            with ThreadPoolExecutor(10) as pool:
                outcomes = [f.result(timeout=30) for f in [pool.submit(attempt) for _ in range(10)]]
            assert outcomes.count(True) == 3
            spent, reserved = subject.month_totals(NOVEMBER)
            assert spent == 0 and reserved == Decimal("0.900000") <= Decimal("1.00")
    finally:
        big.dispose()


def test_reconcile_replaces_the_reservation_by_actual_usage_once(engine: Engine) -> None:
    subject = gate(engine, "1.00")
    job = uuid4()
    held = subject.reserve(job, provider="anthropic", model=MODEL, usd=Decimal("0.5"))
    actual = cost_usd(MODEL, 1000, 100)
    assert actual == Decimal("0.000150")
    subject.reconcile(held, usage())
    assert subject.month_totals(NOVEMBER) == (actual, Decimal(0))
    subject.reconcile(held, usage())  # idempotent: repeating cannot double-count
    assert subject.month_totals(NOVEMBER) == (actual, Decimal(0))
    # The freed amount is available again.
    reserve(subject, "0.999850")
    with pytest.raises(BudgetExhausted):
        reserve(subject, "0.000001")
    with transaction(subject.factory) as session:
        row = session.get(ModelSpend, (job, NOVEMBER))
        assert row is not None and (row.input_tokens, row.output_tokens) == (1000, 100)


def test_release_drops_only_the_hold(engine: Engine) -> None:
    subject = gate(engine, "1.00")
    held = subject.reserve(uuid4(), provider="anthropic", model=MODEL, usd=Decimal("0.7"))
    subject.release(held)
    subject.release(held)
    assert subject.month_totals(NOVEMBER) == (Decimal(0), Decimal(0))


def test_a_retry_keeps_the_possibly_billed_hold_as_uncertain(engine: Engine) -> None:
    subject = gate(engine, "2.00")
    job = reserve(subject, "0.700000")  # first call times out: the hold stays
    held = subject.reserve(job, provider="anthropic", model=MODEL, usd=Decimal("0.500000"))
    assert subject.uncertain_usd(NOVEMBER) == Decimal("0.700000")
    assert subject.month_totals(NOVEMBER) == (Decimal(0), Decimal("0.500000"))
    assert subject.committed_usd(NOVEMBER) == Decimal("1.200000")
    subject.reconcile(held, usage())  # the retry's actual cost replaces only its own hold
    actual = cost_usd(MODEL, 1000, 100)
    assert subject.uncertain_usd(NOVEMBER) == Decimal("0.700000")
    assert subject.committed_usd(NOVEMBER) == Decimal("0.700000") + actual
    subject.reconcile(held, usage())  # repeating changes nothing
    assert subject.committed_usd(NOVEMBER) == Decimal("0.700000") + actual
    # release clears only the current hold, never an uncertain amount.
    again = subject.reserve(job, provider="anthropic", model=MODEL, usd=Decimal("0.1"))
    subject.release(again)
    assert subject.uncertain_usd(NOVEMBER) == Decimal("0.700000")
    assert subject.committed_usd(NOVEMBER) == Decimal("0.700000") + actual


def test_exhaustion_counts_uncertain_and_settled_amounts(engine: Engine) -> None:
    subject = gate(engine, "1.00")
    job = reserve(subject, "0.400000")
    reserve(subject, "0.200000", job)  # 0.4 uncertain + 0.2 held
    assert subject.committed_usd(NOVEMBER) == Decimal("0.600000")
    with pytest.raises(BudgetExhausted):
        reserve(subject, "0.400001")  # another job: 0.6 + 0.400001 > 1.00
    reserve(subject, "0.400000")  # exactly fits
    # A third attempt of the first job: 0.2 more becomes uncertain, so 0.4 + 0.2 + 0.2 held
    # plus the other job's 0.4 would reach 1.2 and is refused.
    with pytest.raises(BudgetExhausted):
        reserve(subject, "0.200000", job)
    # The refusal still kept the previous hold as uncertain rather than dropping it.
    assert subject.uncertain_usd(NOVEMBER) == Decimal("0.600000")
    assert subject.month_totals(NOVEMBER) == (Decimal(0), Decimal("0.400000"))
    assert subject.committed_usd(NOVEMBER) == Decimal("1.000000")


def test_concurrent_reserves_of_one_job_never_lose_a_hold(
    engine: Engine, database_url: str
) -> None:
    big = build_engine(Settings(database_url=SecretStr(database_url), database_pool_size=8))
    try:
        subject = gate(big, "10.00")
        job = uuid4()
        barrier = Barrier(8)

        def attempt() -> None:
            barrier.wait(timeout=10)
            subject.reserve(job, provider="anthropic", model=MODEL, usd=Decimal("0.5"))

        with ThreadPoolExecutor(8) as pool:
            for future in [pool.submit(attempt) for _ in range(8)]:
                future.result(timeout=30)
        assert subject.month_totals(NOVEMBER) == (Decimal(0), Decimal("0.5"))
        assert subject.uncertain_usd(NOVEMBER) == Decimal("3.5")
        assert subject.committed_usd(NOVEMBER) == Decimal("4.0")
    finally:
        big.dispose()


def test_months_are_utc_calendar_months(engine: Engine) -> None:
    last = gate(engine, "1.00", at(2026, 11, 30, 23, 59))
    first = gate(engine, "1.00", at(2026, 12, 1, 0))
    reserve(last, "0.900000")
    with pytest.raises(BudgetExhausted):
        reserve(last, "0.200000")
    reserve(first, "0.900000")  # a new month starts with an empty budget
    assert last.month_totals(NOVEMBER) == (Decimal(0), Decimal("0.900000"))
    assert last.month_totals(DECEMBER) == (Decimal(0), Decimal("0.900000"))
    # An offset timestamp is converted to UTC: 23:30 at UTC-02:00 on 30 November is December 1.
    late = datetime.fromisoformat("2026-11-30T23:30:00-02:00")
    assert (
        gate(engine, "1.00", late)
        .reserve(uuid4(), provider="anthropic", model=MODEL, usd=Decimal("0.05"))
        .month
        == DECEMBER
    )


def test_warnings_are_logged_when_spend_crosses_fifty_and_eighty_percent(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    subject = gate(engine, "10.00")
    caplog.set_level(logging.WARNING, logger="athlete_platform.infrastructure.budget")
    reserve(subject, "4.000000")
    assert caplog.records == []
    reserve(subject, "1.000000")  # exactly 50%
    assert [r.getMessage().split(" for ")[0] for r in caplog.records] == ["Model budget passed 50%"]
    reserve(subject, "2.000000")  # 70%: nothing new
    assert len(caplog.records) == 1
    held = subject.reserve(uuid4(), provider="anthropic", model=MODEL, usd=Decimal("1.5"))
    assert len(caplog.records) == 2 and "80%" in caplog.records[1].getMessage()  # 85%
    subject.release(held)
    reserve(subject, "1.500000")  # crosses 80% a second time
    assert len(caplog.records) == 3
    assert all("sk-" not in r.getMessage() for r in caplog.records)


class BudgetedFake:
    """Fake provider with a fixed worst-case estimate; plays a script of outcomes."""

    def __init__(self, *script: str, estimate: str = "0.004000") -> None:
        self.script = list(script)
        self.estimate_usd = Decimal(estimate)
        self.calls = 0

    def estimate(self, request: ExtractionRequest) -> CostEstimate:
        return CostEstimate(provider="anthropic", model=MODEL, usd=self.estimate_usd)

    def extract(self, request: ExtractionRequest) -> ExtractionOutput:
        self.calls += 1
        step = self.script.pop(0) if self.script else "ok"
        if step == "transient":
            raise TransientExtractionError
        if step == "rejected":
            raise TransientExtractionError(outcome_unknown=False)
        if step == "config":
            raise ExtractionConfigurationError
        if step == "invalid":
            raise InvalidExtractionOutput(usage=usage())
        if step == "invalid-no-usage":
            raise InvalidExtractionOutput
        return ExtractionOutput(content=CONTENT, usage=usage(), prompt_version="synthetic-1")


@pytest.fixture
def world(engine: Engine) -> Iterator[World]:
    yield World(engine)


def budget_for(world: World, budget: str = "25") -> PostgresBudget:
    return PostgresBudget(world.factory, Decimal(budget))


def this_month() -> date:
    now = datetime.now(UTC)
    return date(now.year, now.month, 1)


def test_handler_reserves_calls_reconciles_and_records(world: World) -> None:
    source, _ = world.submit("one")
    subject = budget_for(world)
    provider = BudgetedFake()
    assert run_worker(world.jobs, extraction_handler(world.factory, provider, subject)) == 1
    job = world.job(source.id)
    assert job.state == JobState.SUCCEEDED and job.result == {"interpretation_version": 1}
    assert provider.calls == 1
    assert subject.month_totals(this_month()) == (cost_usd(MODEL, 1000, 100), Decimal(0))
    assert len(world.revisions(source.id)) == 1


def test_exhausted_budget_fails_the_job_without_a_provider_call(world: World) -> None:
    source, _ = world.submit("one")
    subject = budget_for(world, "0.01")
    reserve(subject, "0.009000", uuid4())
    provider = BudgetedFake()
    assert run_worker(world.jobs, extraction_handler(world.factory, provider, subject)) == 1
    job = world.job(source.id)
    assert job.state == JobState.FAILED and job.error_code == FailureCode.BUDGET_EXHAUSTED
    assert job.attempts == 1
    assert provider.calls == 0 and world.revisions(source.id) == []
    view = world.sources.read(world.athlete_id, source.id)
    assert view.extraction is not None
    assert (view.extraction.state, view.extraction.error_code) == ("failed", "budget_exhausted")
    assert subject.month_totals(this_month()) == (Decimal(0), Decimal("0.009000"))


def test_uncertain_failure_keeps_its_hold_and_the_retry_adds_to_it(world: World) -> None:
    source, _ = world.submit("one")
    subject = budget_for(world)
    provider = BudgetedFake("transient", "ok")
    handler = extraction_handler(world.factory, provider, subject)
    assert run_worker(world.jobs, handler) == 1
    job = world.job(source.id)
    assert job.state == JobState.DEFERRED and job.error_code == FailureCode.TRANSIENT
    assert subject.month_totals(this_month()) == (Decimal(0), Decimal("0.004000"))
    make_due(world.engine, job.id)
    assert run_worker(world.jobs, handler) == 1
    assert world.job(source.id).state == JobState.SUCCEEDED
    # The timed-out call stays counted as uncertain; the retry's hold was settled by its usage.
    actual = cost_usd(MODEL, 1000, 100)
    assert subject.month_totals(this_month()) == (actual, Decimal(0))
    assert subject.uncertain_usd(this_month()) == Decimal("0.004000")
    assert subject.committed_usd(this_month()) == Decimal("0.004000") + actual
    with transaction(world.factory) as session:
        assert len(session.scalars(select(ModelSpend)).all()) == 1


@pytest.mark.parametrize(
    "step,held",
    [
        ("rejected", Decimal(0)),  # provider certainly did not process the request
        ("config", Decimal(0)),
        ("invalid-no-usage", Decimal("0.004000")),  # a billed call without usage stays held
    ],
)
def test_reservation_after_other_outcomes(world: World, step: str, held: Decimal) -> None:
    world.submit("one")
    subject = budget_for(world)
    run_worker(world.jobs, extraction_handler(world.factory, BudgetedFake(step), subject))
    assert subject.month_totals(this_month()) == (Decimal(0), held)


def test_configuration_error_fails_terminally_as_handler_error(world: World) -> None:
    source, _ = world.submit("one")
    run_worker(
        world.jobs, extraction_handler(world.factory, BudgetedFake("config"), budget_for(world))
    )
    job = world.job(source.id)
    assert job.state == JobState.FAILED and job.error_code == FailureCode.HANDLER_ERROR
    assert job.attempts == 1


def test_invalid_output_with_usage_is_reconciled(world: World) -> None:
    source, _ = world.submit("one")
    subject = budget_for(world)
    run_worker(world.jobs, extraction_handler(world.factory, BudgetedFake("invalid"), subject))
    assert world.job(source.id).error_code == FailureCode.INVALID_OUTPUT
    assert subject.month_totals(this_month()) == (cost_usd(MODEL, 1000, 100), Decimal(0))


def test_lost_lease_before_the_call_releases_the_reservation(world: World) -> None:
    world.submit("one")
    subject = budget_for(world)
    provider = BudgetedFake()
    lease = world.jobs.claim()
    assert lease is not None
    handler = extraction_handler(world.factory, provider, subject)
    with pytest.raises(Exception, match="Lease lost"):
        handler(lease, lambda: False)
    assert provider.calls == 0
    assert subject.month_totals(this_month()) == (Decimal(0), Decimal(0))


def test_anthropic_extractor_end_to_end_with_a_fake_client(world: World) -> None:
    source, _ = world.submit("one")
    subject = budget_for(world)
    client = FakeClient(message("end_turn", output_tokens=200))
    extractor = AnthropicExtractor(client, MODEL)
    assert run_worker(world.jobs, extraction_handler(world.factory, extractor, subject)) == 1
    assert world.job(source.id).state == JobState.SUCCEEDED
    (call,) = client.messages.calls
    assert call["max_tokens"] == 8000 and "tools" not in call
    (revision,) = world.revisions(source.id)
    assert revision.provenance is not None
    assert revision.provenance["provider"] == "anthropic" and revision.provenance["model"] == MODEL
    assert revision.provenance["prompt_version"] == "extract-text-v1"
    spent, reserved = subject.month_totals(this_month())
    assert spent == cost_usd(MODEL, 1000, 200) and reserved == 0
