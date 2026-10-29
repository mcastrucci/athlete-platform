# Pilot technical plan

- Status: implementation started; service foundation in PR #9 (issue #4). Profile access/versioning is implemented in PR #10 (issue #5); training imports and evaluation remain unimplemented.
- Date: 2026-10-27.
- Product scope: [Pilot PRD](../product/pilot-prd.md).
- Decisions confirmed by the product owner on 2026-10-27: Python, FastAPI, PostgreSQL, SQLAlchemy, Alembic, private file storage, and durable background processing without graph orchestration.
- Hosting decision confirmed by the product owner on 2026-10-27: Cloud Run + Neon for the pilot; retain portability to Cloud SQL.
- Decisions confirmed by the product owner on 2026-10-29: Firebase Authentication; provider APIs with server-held keys and per-task model routing, starting with the Anthropic API and Claude Haiku 5.5; a USD 25 monthly model budget across providers. Remaining limits, the accuracy threshold, and provider retention stay open. See [auth-and-extraction-model.md](auth-and-extraction-model.md).
- Proposed implementation details pending review: PostgreSQL-backed job queue, private Cloud Storage buckets, on-demand Cloud Run Job execution, and the logical schema below. Client technology remains open.
- Decision status: approval of the stack does not establish recruitment, reviewer availability, retention settings, or all PRD D1–D7 decisions.
- Working principle: explicit application workflows and persisted state; no graph orchestration framework is required for the pilot.

## 1. Architecture and boundaries

Propose a modular monolith with a Cloud Run API service and an on-demand worker using the same codebase. Neon PostgreSQL stores application data and durable jobs. Private object storage holds uploaded binaries and generated downloadable artifacts. The worker must finish and exit rather than remain deployed as an idle polling process.

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

Row-level security is an optional defense-in-depth decision for S1, not a pilot requirement assumed by this plan. If adopted, set athlete context transaction-locally from the verified principal, use a non-owner/non-bypass application role, and test missing context, pooled-connection reuse, worker operations, and cross-athlete reads/writes. Do not rely on session-level state surviving transaction pooling. Application authorization and ownership constraints remain required either way.

Use UUID identifiers, UTC timestamps for instants, an IANA athlete timezone, and local dates for training-day concepts. Preserve both recording time and observation time. Do not infer exact session timing from a date without labeling the assumption.

An untimed observation must not receive an invented timestamp that makes it newer than a real dated observation. Keep reported state and historical peak separate. Where the existing conservative pain policy applies, a lower untimed check-in cannot suppress a higher same-day dated report; an untimed higher report must remain visible as untimed. Test before and after the old assumed daily-close time, explicit dated reassessment, and next-day boundaries. Do not generalize pain precedence to every metric.

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

Run file parsing and rendering in a separate resource-limited subprocess with bounded memory, CPU time, wall time, file descriptors, and temporary output. A crash or hang must fail the job without killing the worker. A subprocess alone is not a security sandbox: restrict inherited environment, credentials, filesystem access, and network capability using the supported execution boundary, and validate that boundary before external uploads.

For XLSX, use hardened XML handling such as openpyxl with defusedxml and test entity-expansion and compressed-archive limits. For Pillow, explicitly set the pixel guard to the configured 25 MP limit, reject decompression-bomb warnings/errors, and independently reject decoded dimensions above the limit. Do not rely on library defaults.

Use checksums and athlete-scoped idempotency keys to detect repeated requests. A repeated file is shown as a possible duplicate; checksum equality must not merge legitimately repeated sessions across different dates.

Store a source only after validation/finalization. Object storage and SQL do not share a transaction: use upload states, retryable finalization, and cleanup of abandoned objects. A job cannot process an unfinalized source.

Treat all uploaded text as untrusted data, including instructions embedded in a document. The extraction model has no tools for arbitrary network access or database mutation. Pass only necessary athlete context; validate structured outputs and limit retries.

The model extracts and proposes; deterministic code validates structure, computes supported dose metrics, and checks explicit constraints. Validate proposed changes before presentation and again before acceptance. Failed hard checks block activation. An unresolved dose produces an incomplete/manual-review result, not a successful zero-load calculation.

Separate deterministic constraints from estimation models. Residual-load half-lives, metcon repetitions-to-set equivalences, volume indices, and muscle mappings are model assumptions, not measured physiology. Store versioned model definitions and parameter sets with provenance, limits, and expert-review status. Bind the selected version and any athlete-specific override to each evaluation manifest.

Do not ship legacy numeric assumptions as silently active universal defaults. Athlete-specific parameters require a rationale; shared mappings may remain shared and versioned, without pretending to be individually calibrated. Label estimates in every relevant output and preserve original quantities. S7 expert review must approve the models and their limits for external use; unsupported models remain disabled or explicitly incomplete.

Generate explanations from structured findings, references, and rule outcomes. Distinguish measured values, model estimates, and coaching hypotheses. Store only verified evidence references; do not request or persist hidden chain-of-thought.

## 5. Durable jobs

For the pilot, propose a small PostgreSQL-backed queue so there is no additional broker. Keep this adapter replaceable if operational needs later justify a maintained queue system.

Jobs hold type, athlete, resource revision, idempotency key, status, available time, attempts, lease owner/token, lease expiry, and a sanitized failure code. Keep detailed attempt history and provider request identifiers where available.

Claim eligible jobs atomically with row locking and `SKIP LOCKED`, set a lease, and commit before external work. Never hold a SQL transaction open during model calls or file processing.

Use heartbeats and lease expiry for recovery. A fencing token prevents an expired worker from publishing results after another worker has reclaimed the job. Commit output and success state atomically with an ownership check.

Delivery is at least once, not exactly once. Unique constraints and handler idempotency prevent duplicate durable outputs. External model calls may repeat after an ambiguous timeout; record usage and bound attempts rather than promising exactly-once billing.

After the enqueue transaction commits, request an authenticated Cloud Run Job execution using a least-privilege service identity. Treat this dispatch as best effort: the database job is the durable record, not the launch request. Proposed recovery: one Cloud Scheduler trigger every 15 minutes starts a bounded worker execution to recover missed launches, expired leases, and due retries. Record this worst-case recovery delay in the interface; the precise interval is configurable.

The worker drains eligible work within a bounded runtime and then exits. If no work is due, exit without polling or holding a database connection; future retries are recovered by a later execution. Do not use LISTEN connections, database health probes, or rapid recurring polling that prevent Neon from suspending. Multiple launches may overlap, so row claims and fencing remain mandatory.

Use bounded exponential backoff with jitter for transient failures. Invalid files and unresolved user input do not retry indefinitely. Expose terminal failure and an explicit retry action. Deleted athletes/resources cannot be republished by in-flight jobs.

## 6. API, access, and client boundary

Expose resource-oriented endpoints under `/v1` for profile/goals, sources, interpretations, blocks/plans, logs/observations, evaluations/proposals, reviews, jobs, and export/deletion requests.

Explicit commands include confirm interpretation, request evaluation, accept/reject proposal, and activate next plan. Long work returns an accepted response with a job/resource identifier; mutations use expected versions and idempotency keys where retries can duplicate work.

Resolve athlete access from a verified identity and an ownership check. Never trust a submitted `athlete_id` as authorization. Keep athlete scope in application services and repositories, including worker jobs, exports, object access, and deletion.

Use a managed authentication provider selected before external onboarding. Define the identity adapter and authenticated principal contract now; local test identities must not be enabled in deployed environments.

The mobile-friendly client must support the complete P1–P6 flow. An API alone does not satisfy the pilot. Frontend technology and the assisted-operation interface remain open decisions; select them before the client stage. Document any operator action and keep it distinct from athlete confirmation.

## 7. Reusing the existing engine

The existing `mcastrucci/athlete` repository already exposes `TrainingRepository`, `ArtifactStore`, and a synthetic in-memory implementation.

Extraction baseline: [athlete commit 3f5897c](https://github.com/mcastrucci/athlete/commit/3f5897cf6a2fc16acc5c6794e6d8caed2fbab00f). [PR #8](https://github.com/mcastrucci/athlete/pull/8) is already merged at `924382a2bafbe8d319a10b7da26b89172ba2e33f`; its rolling-volume and unknown-dose behavior is included in this baseline. Use the pinned source, not a moving branch, for parity comparisons. Reuse domain behavior and relevant regression cases after a dependency and data review.

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

Deploy the API on Cloud Run with minimum instances set to zero and an explicit maximum instance count. Use Neon for managed PostgreSQL. Propose a private Cloud Storage bucket and on-demand Cloud Run Jobs for the worker, with the recovery scheduler described in section 5. This documentation change provisions no resources.

Use nearby compatible regions and account for cross-provider latency and egress. Configure TLS, a small per-process connection pool and bounded aggregate connections across API and workers. Use Neon's pooled endpoint for normal application traffic where compatible; use a direct connection for Alembic and dump/restore operations. Validate connection recovery after suspension and cold-start latency.

Authentication, model providers/routing, retention settings, and exact resource limits remain deployment decisions. Model selection is task-specific behind provider adapters, with configured input/output/reasoning limits and fallback budgets.

### Portability to Cloud SQL

Use standard PostgreSQL through SQLAlchemy and Alembic; keep credentials, pooled/direct DSNs, SSL, and connection limits configurable. Avoid Neon-specific SQL or runtime dependencies. Check PostgreSQL major-version and extension compatibility before enabling any extension.

Document and rehearse a small-dataset migration: stop new writes and dispatch, drain/fence in-flight work, take a consistent dump, restore to Cloud SQL, configure roles and connectivity, validate row counts, constraints, migrations, and representative evaluations, then switch connections and resume work. Object storage stays independent.

Retain Neon as a read-only rollback source until validation is complete. Switching back is simple only before new writes reach Cloud SQL; after that, reconcile or replay new writes explicitly rather than discarding them. A migration needs a planned write pause, not just a connection-string change.

Use environment-managed secrets, HTTPS, private buckets, short-lived authorized download URLs, and logs containing identifiers/error codes rather than raw uploads or sensitive prompts.

Back up PostgreSQL and define object retention consistently. Exercise a restore with synthetic data. Export includes originals, confirmed data, and revision-linked outputs in documented formats.

Deletion blocks new work, cancels pending jobs, prevents late writes, deletes derived data and objects, and records completion without retaining deleted content. Provider retention and backup expiry must be documented before onboarding. Immutable history means normal edits do not overwrite it; it does not override a deletion request.

Enforce configurable per-athlete daily quotas for uploads/bytes, extraction attempts, and evaluation attempts, including retries and model fallbacks. Reserve quota and estimated model spend atomically before dispatch; reconcile usage after completion. Bound input tokens, output/reasoning tokens, attempts, and concurrent provider calls. An uncertain timeout retains a conservative spend reservation until reconciled.

Add global model-spend warning thresholds and an application budget gate that blocks new calls when the configured allowance is exhausted. Cloud billing alerts are notifications, not hard spending caps; delayed provider usage can still create a small overrun. Choose numeric quotas and budgets before external onboarding.

Quota-limited work has a visible deferred state, reset/retry time, and optional authorized override. Do not lose the confirmed input or retry in a tight loop. Recheck quotas at execution so parallel submissions cannot bypass them.

Track job age, attempts, failures, extraction corrections, provider latency/cost, and manual assistance. Add request/job correlation identifiers. Store pilot evaluation events using the PRD's definitions without logging private training text.

## 9. Implementation stages

GitHub Issues are the source of truth for task status and dependencies. Each implementation PR links its issue and records validation evidence; use closing keywords only when all acceptance criteria are met. This document retains architecture, stage-level scope, and links, without duplicating issue checklists.

### First vertical slice

The first coding task is [#4](https://github.com/mcastrucci/athlete-platform/issues/4). Decision work in #3 can proceed alongside it.

| Issue | Verifiable outcome | Stage coverage |
| --- | --- | --- |
| [#3](https://github.com/mcastrucci/athlete-platform/issues/3) | Record initial authentication and extraction-model decisions | Part of S0 |
| [#4](https://github.com/mcastrucci/athlete-platform/issues/4) | Start the local service, migrate PostgreSQL, and pass CI | Foundation portion of S1 |
| [#5](https://github.com/mcastrucci/athlete-platform/issues/5) | Own and update an isolated, versioned athlete profile | Identity/context portions of S1–S2 |
| [#6](https://github.com/mcastrucci/athlete-platform/issues/6) | Submit text, revise its interpretation, and confirm sessions without duplication | Text-only subset of S2–S4 |
| [#11](https://github.com/mcastrucci/athlete-platform/issues/11) | Durable queue prerequisite in [PR #12](https://github.com/mcastrucci/athlete-platform/pull/12); source/interpretation/confirmation remain in #6 | Queue subset of S3 |
| [#7](https://github.com/mcastrucci/athlete-platform/issues/7) | Evaluate confirmed training with version-linked findings | Evaluation subset of S2/S4 |

These issues do not complete their entire parent stages. Other formats, frontend, full proposal workflows, weekly reviews, deployment, and external-pilot readiness remain in the stage plan below. Create further actionable issues as those stages approach, rather than treating these five as the complete pilot backlog. If a task requires multiple reviewable PRs, split out linked prerequisites before implementation.

### Stage reference

Each stage gets one or more reviewable PRs. Replace “Pending” with implementation PR links and completion evidence; task status remains in Issues.

| Stage | Scope and PRD mapping | Required validation | PR |
| --- | --- | --- | --- |
| S0 | Record Cloud Run + Neon decision; close remaining choices: storage, auth/models, client, reviewer/operations, upload limits and budgets; D1–D7 | Dated decision record; distinguish technical approval from recruitment and expert availability | Pending |
| S1 | Project foundation, API/worker entry points, PostgreSQL, migrations, identity/scope; P1 | Fresh database migration; two-user isolation including cross-athlete references; local startup; explicit RLS decision | Foundation: [PR #9](https://github.com/mcastrucci/athlete-platform/pull/9); profiles/identity: [PR #10](https://github.com/mcastrucci/athlete-platform/pull/10); provider onboarding remains in issue #3 |
| S2 | Typed training model, profile/context, canonical revisions, engine extraction; P1/P3 | Synthetic parity cases and transaction rollback; unknown vs zero; timezone cases | Pending |
| S3 | Sources, object storage adapter, durable queue and imports; P2 | Every input format; corrupt/ambiguous files; parser isolation and resource limits; duplicate request; missed dispatch, lease recovery, stale worker fencing and quota races | Queue: [PR #12](https://github.com/mcastrucci/athlete-platform/pull/12); remaining imports stay in issue #6 |
| S4 | Corrections, confirmation, evaluation and proposal lifecycle; P2–P4 | Idempotent confirmation; input manifests; hard constraints; stale output and concurrent acceptance rejection | Pending |
| S5 | Execution, observations and weekly review; P5/P6 | Partial and unplanned sessions; historical corrections; next-week review preserves block intent | Pending |
| S6 | Complete mobile-friendly client and assisted review flow; P1–P6 | Athlete completes upload-to-review loop; clear unknowns, failures and version conflicts; explanation access | Pending |
| S7 | Pilot readiness, export/deletion, deployment, engine consumer migration | Restore and portable PostgreSQL migration rehearsal, deletion during active job, quotas/spend gate, expert review of estimation models, end-to-end synthetic run | Pending |

- [x] Record the proposed technical plan in [PR #2](https://github.com/mcastrucci/athlete-platform/pull/2).
- [x] Record the owner's 2026-10-27 Cloud Run + Neon hosting decision; remaining S0 choices are still open.
- [ ] Resolve S0 decisions.
- [ ] Run the pilot and compare results with the PRD hypotheses.

Implementation task status is tracked in the linked issues. S7 remains a required gate before inviting external participants.

Stages express dependencies, not delivery dates. The first useful vertical slice is one synthetic athlete importing text, confirming a session, and receiving a traceable evaluation; expand formats and proposal workflows from there.

## 10. Verification and reference material

Use unit tests for domain decisions and PostgreSQL integration tests for JSONB, constraints, transactions, job claiming, and revision conflicts. SQLite is not a substitute for those database behaviors. Use provider fakes in ordinary tests and a small controlled evaluation corpus for real model extraction quality.

Pin dependencies at implementation time. Run linting, type checks, tests, and migration checks in CI. Documentation-only changes require link/content review rather than application tests.

Primary references for the implementation choices:

- [PostgreSQL JSON types](https://www.postgresql.org/docs/current/datatype-json.html).
- [PostgreSQL SELECT and locking](https://www.postgresql.org/docs/current/sql-select.html): SKIP LOCKED is suitable for queue consumers, not general consistent reads.
- [SQLAlchemy session basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html): transaction and session ownership.
- [Alembic documentation](https://alembic.sqlalchemy.org/en/latest/).
- [Cloud Run Jobs scheduling](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule).
- [Neon connection pooling](https://neon.com/docs/connect/connection-pooling).
- [openpyxl parser security](https://openpyxl.readthedocs.io/en/stable/).
- [Pillow image limits](https://pillow.readthedocs.io/en/stable/reference/Image.html).
- [FastAPI background tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/): the durable pipeline is implemented by the worker, not in-process response callbacks.
