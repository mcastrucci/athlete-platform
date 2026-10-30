# Text sources and interpretations

Date: 2026-10-30. First slice of [issue #6](https://github.com/mcastrucci/athlete-platform/issues/6), after the queue in #11. It adds no model provider, job wiring, confirmation, or canonical sessions.

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

1. Enqueue extraction on submission (or after a `keep` decision) and add the Anthropic adapter behind the extraction contract, with the budget gate and a fake for tests.
2. Confirmation that materializes canonical sessions transactionally, with stale-confirmation conflicts.
