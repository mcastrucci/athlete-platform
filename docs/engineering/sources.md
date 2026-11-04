# Text sources and interpretations

Date: 2026-10-30. First slice of [issue #6](https://github.com/mcastrucci/athlete-platform/issues/6), after the queue in #11. The second slice adds extraction enqueueing and a worker handler behind a provider-neutral contract. There is still no model provider (tests use a fake extractor), confirmation, or canonical sessions.

## Sources

An athlete submits pasted training text with an idempotency key. The text is stored exactly as received, at most 20,000 characters, and never edited afterward.

| Request | Result |
| --- | --- |
| New key | 201 with the new source |
| Same key, same text | 200 with the stored source |
| Same key, different text | 409 |
| New key, text identical to an earlier source that was not discarded | 201 with `duplicate_of` set and `duplicate_status: pending` |

Duplicate detection compares an exact SHA-256 digest of the text. Any edit, including whitespace, makes the texts distinct, so the same training written for different dates is not flagged. A pending duplicate waits for the athlete: `keep` or `discard` through the duplicate-decision endpoint. Repeating the same decision is idempotent. Changing a decision, or deciding on a source that is not a pending duplicate, returns 409. Discarded sources are never chosen as duplicate targets and cannot receive interpretations.

Submissions for one athlete serialize on the athlete row, so concurrent submissions see each other's commits. A concurrent submission that loses the idempotency race is resolved against the stored text.

## Extraction

Enqueue and state:

- After a submission commits, a source whose duplicate status is `none` or `kept` gets the queue job `extract-source:{source_id}` with payload `{"source_id": …}`. This runs on every submit call that returns the source, including idempotent replays, so a lost enqueue heals when the client retries. Queue idempotency (athlete, key, payload hash) makes repeats return the same job. A `keep` decision, including a repeated one, enqueues the same way. Pending and discarded duplicates never enqueue.
- `SourceView.extraction` is `{job_id, state, error_code}` read from that job, or `null` when no job exists. `state` is a queue state (`queued`, `running`, `deferred`, `succeeded`, `failed`).
- `QueueFull` never loses or rolls back the stored source. The enqueue happens after the source commits, and the response carries `{job_id: null, state: "not_queued", error_code: "queue_full"}`. That marker appears only on the submit (or keep) response; a later read shows `null`. Repeating the same submission enqueues once capacity returns.

The `Extractor` contract (`application/extraction.py`) takes an `ExtractionRequest` (source ID, text, athlete timezone) and returns an `ExtractionOutput` (interpretation content, provider/model usage, prompt version). It raises `TransientExtractionError` or `InvalidExtractionOutput`.

Handler (`worker/extraction.py`, `extraction_handler(factory, extractor)`), per lease:

1. Read `source_id` from the payload and load the source scoped to the leased job's athlete. A malformed, missing, or foreign source fails the job terminally (`handler_error`) without a write.
2. If this job already recorded a revision, return its version (duplicate delivery and reclaim are idempotent). A pending or discarded duplicate, or a source interpreted by another run, fails terminally without a write.
3. Renew the lease before the provider call and again before writing; a lost lease stops without a write. No database transaction is open during the provider call.
4. A transient extractor error defers the job with queue backoff. Invalid output (`InvalidExtractionOutput`, or content failing `InterpretationContent` validation) is `invalid_output`: retried once when it happens on attempt 1, terminal afterwards. Attempts after the first never retry invalid output, even if the job was retried manually.
5. Record through `PostgresExtractionResults.record` with the job ID as run ID and return `{"interpretation_version": n}`.

The default worker still configures no handler; wiring a real provider is a later slice.

## Interpretations

Interpretations are append-only revisions of one source.

- `extraction` revisions are written by worker code through `PostgresExtractionResults`, never through HTTP. Each carries provenance: run ID, provider, model, prompt version, and schema version. A repeated run ID returns the existing revision, so handlers pass the job ID as the run ID and every retry of one job is idempotent. Only the first extraction of a source is recorded: once a source has an interpretation, a new run ID is refused, so a later run can never displace an earlier result or an athlete's correction. Extraction is also refused while a source is a pending or discarded duplicate.
- `correction` revisions come from the athlete through `PUT …/interpretation` with `expected_version`. A stale version, a source without an interpretation yet, or a discarded source returns 409. Every correction records the version it was based on.
- Earlier revisions stay readable with `?version=`.

The schema keeps unknown and zero distinct: `null` means not reported, and an explicit `0` means zero. Quantities keep the unit as written and are never converted here. Session status is `planned`, `performed`, or `unknown`; nothing in an interpretation marks work as confirmed. Ambiguities are explicit fields rather than guesses. Unknown fields are rejected.

## Endpoints

All paths are under `/v1/athletes/{athlete_id}` and require the caller to own that athlete. Another athlete's sources return 404.

| Method and path | Purpose |
| --- | --- |
| `POST /sources` | Submit text (`idempotency_key`, `text`) |
| `GET /sources/{source_id}` | Read the original text and duplicate state |
| `POST /sources/{source_id}/duplicate-decision` | `{"decision": "keep" \| "discard"}` |
| `GET /sources/{source_id}/interpretation` | Latest revision, or `?version=n` |
| `PUT /sources/{source_id}/interpretation` | Correction with `expected_version` and full `content` |

## Next slices in #6

1. Add the Anthropic adapter behind the extraction contract, with the budget gate, and configure it in the worker (usage is returned by the contract but not yet stored).
2. Confirmation that materializes canonical sessions transactionally, with stale-confirmation conflicts.
