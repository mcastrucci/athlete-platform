# Pilot technical plan

- Status: proposed implementation plan; no application code has been implemented.
- Date: 2026-10-27.
- Product scope: [Pilot PRD](../product/pilot-prd.md).
- Agreed technical direction: Python, FastAPI, PostgreSQL, SQLAlchemy, Alembic, private object storage, and a durable worker in the same project.
- Working principle: explicit application workflows and persisted state; no graph orchestration framework is required for the pilot.

## 1. Architecture and boundaries

Build a modular monolith deployed as two processes from the same codebase: API and worker. PostgreSQL stores application data and durable jobs. Private object storage holds uploaded binaries and generated downloadable artifacts.

The API authenticates users, authorizes athlete access, validates requests, and invokes application services. The worker performs extraction, model calls, evaluation, and report generation. Both use the same domain and persistence contracts.

Keep a small package structure:

| Location | Responsibility |
| --- | --- |
| `src/athlete_platform/api/` | FastAPI routes, authentication integration, HTTP schemas |
| `src/athlete_platform/application/` | Use cases, transactions, authorization context, workflows |
| `src/athlete_platform/domain/` | Training entities, dose calculations, constraints, findings |
| `src/athlete_platform/infrastructure/` | SQLAlchemy repositories, object storage, model adapters, job persistence |
| `src/athlete_platform/worker/` | Job claiming, retries, heartbeat, handlers |
| `tests/` | Synthetic domain, PostgreSQL integration, provider contract, and workflow cases |
| `migrations/` | Reviewed Alembic migrations |

The domain imports neither FastAPI nor SQLAlchemy and has no dependency on a model provider. Pydantic validates API and extraction contracts. SQLAlchemy models are persistence details, not the API contract.

Start with synchronous SQLAlchemy sessions scoped to a request or job; do not share sessions across concurrent work. Avoid blocking calls inside asynchronous route handlers. Select supported Python and dependency versions together during the foundation stage and commit a lockfile.

No microservices, separate vector database, Redis, general-purpose workflow engine, or event sourcing in the initial design. Add infrastructure only for an observed requirement.

## 2. Persistence model

Preserve the previous repository's concepts, not its file layout. Use relational columns for identity, ownership, dates, references, status, and frequently queried training data; TEXT for narratives; versioned, validated JSONB for variable structures.

Proposed logical entities are grouped below. These are design boundaries, not a requirement to create every table in the first migration.

| Group | Entities and contents |
| --- | --- |
| Identity and profile | `users`, `athletes`, versioned profile/goals/constraints/availability; timezone and ownership |
| Catalog | Exercises, aliases, movement patterns, capacity mappings, and versioned rules; athlete-specific overrides separate from shared definitions |
| Sources | `source_documents`: raw text or private object reference, checksum, media type, byte size, uploader, upload state |
| Extraction | `interpretations`: source, run metadata, schema version, extracted JSONB, ambiguities, provenance, parent revision |
| Programming | Development blocks, plan versions, planned sessions and items; dose, intensity, placement, fixed commitments |
| Execution | Session logs and performed items, including unplanned work, partial completion and explicit unknowns |
| Observations | Dated wellness, body metrics, skill assessments and free-text notes |
| Decisions | Evaluation runs, findings, proposals, acceptance/rejection records and weekly reviews |
| Operations | Jobs, attempts, provider usage, audit events, export/deletion requests |

Every athlete-owned record carries `athlete_id`. References between athlete-owned records must also enforce matching ownership, using composite foreign keys where applicable. Shared catalog records have explicit scope; null ownership must not accidentally grant access to private content.

Use UUID identifiers, UTC timestamps for instants, an IANA athlete timezone, and local dates for training-day concepts. Preserve both recording time and observation time. Do not infer exact session timing from a date without labeling the assumption.

### Training structure

A plan version contains sessions; each session contains ordered items such as strength work, technical practice, conditioning, or a metcon. Use explicit item type, exercise references, units, quantities, and intensity fields where stable.

Nested metcon structures can remain in validated JSONB with a schema version. Domain code converts both relational records and nested structures to the same typed training representation before calculation. Do not maintain a second unversioned JSON copy of canonical relational values.

Support absolute load, percentage load with its reference benchmark, duration, distance, repetitions, and calories without flattening them into one unit. Retain unsupported details as source text and flag them for review.

Unknown is not zero. Completion state is distinct from quantity: not logged, skipped, partial, complete, or modified does not by itself establish the performed dose. Multiple observations may exist on one day; a daily summary is derived with an explicit policy.

Indexes initially follow actual access paths: athlete/date, plan/version, source/run, and job/status/available time. Add JSONB indexes only for implemented queries. Raw text alone does not require semantic search; introduce full-text or embedding search if a specific retrieval use case warrants it.

## 3. Source, interpretation, and version lifecycle

Maintain distinct layers:

1. Original source: immutable uploaded file or pasted text until deletion.
2. Interpretation: machine extraction and subsequent user corrections, each as a revision.
3. Confirmed programming: canonical structured version explicitly confirmed by the athlete.
4. Evaluation and proposal: derived outputs tied to exact input revisions.
5. Accepted plan: a version activated by an authorized user.

Record model/provider identifier, prompt template version, parser version, schema version, application/rule version, timestamps, and source locators such as page, cell range, or text span when available. Model confidence is not a calibrated probability; ambiguities and missing fields remain explicit.

Corrections create revisions. Confirming an interpretation materializes its canonical plan data in a transaction; this write is idempotent and must not duplicate sessions. Multiple sources can contribute to a plan, and confirmed manual entries do not require a fabricated upload.

Evaluations reference a frozen input manifest: profile, goals, constraints, plan/log/observation revisions, catalog/rule versions, evaluation time, and relevant missing inputs. Persist structured findings and the generated explanation. This supports auditing; provider output may still vary on a later rerun.

Changing a dependency marks affected outputs stale. Initially invalidate at the athlete/block level where dependency precision is uncertain. Historical results remain inspectable, but stale proposals cannot activate without revalidation.

Acceptance uses an expected base version and a transaction. A concurrent update returns a conflict rather than overwriting work. Preserve the source program and prior active plans. Execution logs reference the performed plan version, with support for unplanned work.

## 4. Import pipeline and model responsibilities

Use an explicit flow: source finalized → extraction queued → interpretation ready or failed → user correction/confirmation → evaluation queued → proposal ready → user acceptance.

A manual confirmation is a persisted boundary, not a suspended worker job. Poll job/resource status from the initial client; streaming updates are optional later.

- Text: preserve verbatim input and parse against the extraction schema.
- XLSX: parse sheets and cell coordinates with a bounded workbook parser; never execute macros or formulas. Missing cached formula values become unresolved input.
- PDF: extract text where available; render bounded pages for scanned or layout-dependent input.
- JPG/PNG: validate decoded dimensions and use a vision-capable extraction adapter where needed.
- All formats: normalize aliases and units without inventing missing dose; surface unknown exercises for resolution.

Proposed configurable upload defaults: 20 MiB per file, 20 PDF pages, 25 megapixels per image, and 100,000 nonempty workbook cells. Validate these defaults against the synthetic acceptance corpus before launch and publish actual limits in the interface. Bound decompression, rendering, execution time, and model context separately; byte limits alone are insufficient.

Use checksums and athlete-scoped idempotency keys to detect repeated requests. A repeated file is shown as a possible duplicate; checksum equality must not merge legitimately repeated sessions across different dates.

Store a source only after validation/finalization. Object storage and SQL do not share a transaction: use upload states, retryable finalization, and cleanup of abandoned objects. A job cannot process an unfinalized source.

Treat all uploaded text as untrusted data, including instructions embedded in a document. The extraction model has no tools for arbitrary network access or database mutation. Pass only necessary athlete context; validate structured outputs and limit retries.

The model extracts and proposes; deterministic code validates structure, computes supported dose metrics, and checks explicit constraints. Validate proposed changes before presentation and again before acceptance. Failed hard checks block activation. An unresolved dose produces an incomplete/manual-review result, not a successful zero-load calculation.

Generate explanations from structured findings, references, and rule outcomes. Distinguish measured values, model estimates, and coaching hypotheses. Store only verified evidence references; do not request or persist hidden chain-of-thought.

## 5. Durable jobs

For the pilot, use a small PostgreSQL-backed queue so there is no additional broker. Keep this adapter replaceable if operational needs later justify a maintained queue system.

Jobs hold type, athlete, resource revision, idempotency key, status, available time, attempts, lease owner/token, lease expiry, and a sanitized failure code. Keep detailed attempt history and provider request identifiers where available.

Claim eligible jobs atomically with row locking and `SKIP LOCKED`, set a lease, and commit before external work. Never hold a SQL transaction open during model calls or file processing.

Use heartbeats and lease expiry for recovery. A fencing token prevents an expired worker from publishing results after another worker has reclaimed the job. Commit output and success state atomically with an ownership check.

Delivery is at least once, not exactly once. Unique constraints and handler idempotency prevent duplicate durable outputs. External model calls may repeat after an ambiguous timeout; record usage and bound attempts rather than promising exactly-once billing.

Use bounded exponential backoff with jitter for transient failures. Invalid files and unresolved user input do not retry indefinitely. Expose terminal failure and an explicit retry action. Deleted athletes/resources cannot be republished by in-flight jobs.

## 6. API, access, and client boundary

Expose resource-oriented endpoints under `/v1` for profile/goals, sources, interpretations, blocks/plans, logs/observations, evaluations/proposals, reviews, jobs, and export/deletion requests.

Explicit commands include confirm interpretation, request evaluation, accept/reject proposal, and activate next plan. Long work returns an accepted response with a job/resource identifier; mutations use expected versions and idempotency keys where retries can duplicate work.

Resolve athlete access from a verified identity and an ownership check. Never trust a submitted `athlete_id` as authorization. Keep athlete scope in application services and repositories, including worker jobs, exports, object access, and deletion.

Use a managed authentication provider selected before external onboarding. Define the identity adapter and authenticated principal contract now; local test identities must not be enabled in deployed environments.

The mobile-friendly client must support the complete P1–P6 flow. An API alone does not satisfy the pilot. Frontend technology and the assisted-operation interface remain open decisions; select them before the client stage. Document any operator action and keep it distinct from athlete confirmation.

## 7. Reusing the existing engine

The existing `mcastrucci/athlete` repository already exposes `TrainingRepository`, `ArtifactStore`, and a synthetic in-memory implementation. Reuse domain behavior and relevant regression cases after a dependency and data review.

Map existing concepts as follows:

| Existing concept | Product equivalent |
| --- | --- |
| Profile, goals, constraints, inventory | Athlete-scoped versioned context |
| Macro, block, week, external coach plan | Blocks, plan versions, source provenance |
| Session YAML and wellness/body CSV | Typed execution and observation records |
| Raw notes and narrative reviews | Source TEXT and versioned review artifacts |
| Generated reports | Derived evaluation outputs and downloadable artifacts |

Do not port filesystem layout, YAML paths as references, CSV-shaped string contracts, personal thresholds, or Max's historical records into the product model.

Extract one reusable engine package within this repository, with a compatibility adapter for the old read contract where useful. Add write-oriented application repositories and transaction boundaries rather than extending the old generic reader into the entire product API.

Validate behavior with synthetic parity cases for rolling volume, unknown dose, partial sessions, dated observations, constraints, and timezone handling. Translate copied code, comments, and test descriptions into English.

Before production use, publish a versioned engine artifact and migrate the personal repository to consume a pinned release through a separate PR there. Avoid maintaining two evolving engine implementations. Extraction and consumer migration must name the source commit and release version; no automatic migration of personal data.

## 8. Operations and data lifecycle

Deploy one API process and one worker using managed PostgreSQL and private object storage. Choose hosting, storage, authentication, and model providers before deployment; do not select paid infrastructure in this documentation PR.

Use environment-managed secrets, HTTPS, private buckets, short-lived authorized download URLs, and logs containing identifiers/error codes rather than raw uploads or sensitive prompts.

Back up PostgreSQL and define object retention consistently. Exercise a restore with synthetic data. Export includes originals, confirmed data, and revision-linked outputs in documented formats.

Deletion blocks new work, cancels pending jobs, prevents late writes, deletes derived data and objects, and records completion without retaining deleted content. Provider retention and backup expiry must be documented before onboarding. Immutable history means normal edits do not overwrite it; it does not override a deletion request.

Track job age, attempts, failures, extraction corrections, provider latency/cost, and manual assistance. Add request/job correlation identifiers. Store pilot evaluation events using the PRD's definitions without logging private training text.

## 9. Implementation stages

Each stage gets one or more reviewable PRs. Replace “Pending” with links and record actual completion evidence.

| Stage | Scope and PRD mapping | Required validation | PR |
| --- | --- | --- | --- |
| S0 | Close implementation-blocking choices: providers, client, reviewer/operations, upload limits; D1–D7 | Dated decision record; distinguish technical approval from recruitment and expert availability | Pending |
| S1 | Project foundation, API/worker entry points, PostgreSQL, migrations, identity/scope; P1 | Fresh database migration; two-user isolation including cross-athlete references; local startup | Pending |
| S2 | Typed training model, profile/context, canonical revisions, engine extraction; P1/P3 | Synthetic parity cases and transaction rollback; unknown vs zero; timezone cases | Pending |
| S3 | Sources, object storage adapter, durable queue and imports; P2 | Every input format; corrupt/ambiguous files; duplicate request; crash, lease recovery and stale worker fencing | Pending |
| S4 | Corrections, confirmation, evaluation and proposal lifecycle; P2–P4 | Idempotent confirmation; input manifests; hard constraints; stale output and concurrent acceptance rejection | Pending |
| S5 | Execution, observations and weekly review; P5/P6 | Partial and unplanned sessions; historical corrections; next-week review preserves block intent | Pending |
| S6 | Complete mobile-friendly client and assisted review flow; P1–P6 | Athlete completes upload-to-review loop; clear unknowns, failures and version conflicts; explanation access | Pending |
| S7 | Pilot readiness, export/deletion, deployment, engine consumer migration | Restore, deletion during active job, provider limits/cost visibility, expert review, end-to-end synthetic run | Pending |

- [x] Record the proposed technical plan.
- [ ] Resolve S0 decisions.
- [ ] Complete S1–S3.
- [ ] Complete S4–S6.
- [ ] Complete S7 before inviting external participants.
- [ ] Run the pilot and compare results with the PRD hypotheses.

Stages express dependencies, not delivery dates. The first useful vertical slice is one synthetic athlete importing text, confirming a session, and receiving a traceable evaluation; expand formats and proposal workflows from there.

## 10. Verification and reference material

Use unit tests for domain decisions and PostgreSQL integration tests for JSONB, constraints, transactions, job claiming, and revision conflicts. SQLite is not a substitute for those database behaviors. Use provider fakes in ordinary tests and a small controlled evaluation corpus for real model extraction quality.

Pin dependencies at implementation time. Run linting, type checks, tests, and migration checks in CI. Documentation-only changes require link/content review rather than application tests.

Primary references for the implementation choices:

- [PostgreSQL JSON types](https://www.postgresql.org/docs/current/datatype-json.html).
- [PostgreSQL SELECT and locking](https://www.postgresql.org/docs/current/sql-select.html): SKIP LOCKED is suitable for queue consumers, not general consistent reads.
- [SQLAlchemy session basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html): transaction and session ownership.
- [Alembic documentation](https://alembic.sqlalchemy.org/en/latest/).
- [FastAPI background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/): the durable pipeline is implemented by the worker, not in-process response callbacks.
