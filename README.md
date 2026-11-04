# athlete-platform

A product to help athletes who already train organize their development: clear goals, volume, periodization, progression, and explained decisions.

Internal working name. The commercial brand is still undecided.

This repository contains product definition, the Python service foundation, and owned, versioned athlete profiles. Training imports, provider onboarding, and deployment are not implemented yet.

## Local development

Prerequisites: Python 3.12, [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13.4, and Docker with Compose v2. Dependencies are pinned in `uv.lock`.

```bash
uv sync --locked
cp .env.example .env
docker compose up -d --wait postgres
uv run --locked alembic upgrade head
uv run --locked alembic check
uv run --locked uvicorn athlete_platform.api.main:create_app --factory --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
curl --fail http://127.0.0.1:8000/health/live
uv run --locked athlete-worker
```

`/health/live` reports process liveness and returns `{"status":"ok"}`. It does not query PostgreSQL or external services and does not certify database readiness. API documentation is available at `/docs`.

The worker CLI is deliberately a bootstrap: it logs that no handlers exist and exits successfully without polling or opening a database connection. The [durable queue and bounded runner](docs/engineering/job-queue.md) are available as Python services; connecting a production extraction handler remains in [issue #6](https://github.com/mcastrucci/athlete-platform/issues/6).

Compose exposes PostgreSQL 17 on `127.0.0.1:5433` with development-only credentials. It stores data in a named volume. Stop it with `docker compose down`; this preserves the volume. Do not use the sample credentials outside local development.

## Configuration and migrations

Settings use the `ATHLETE_` prefix and read `.env` locally; process environment variables take precedence. Keep credentials out of git. A bare process can run health checks and the empty worker without a DSN, but database operations fail with a configuration error until one is provided.

| Variable | Purpose |
| --- | --- |
| `ATHLETE_ENVIRONMENT` | `local`, `test`, or `production`; defaults to `local` |
| `ATHLETE_DATABASE_URL` | Runtime PostgreSQL connection using `postgresql+psycopg://` |
| `ATHLETE_MIGRATION_DATABASE_URL` | Direct migration connection; falls back to the runtime URL when absent |
| `ATHLETE_DATABASE_POOL_SIZE` | Connections per engine; default 2 |
| `ATHLETE_DATABASE_MAX_OVERFLOW` | Extra connections per engine; default 0 |
| `ATHLETE_DATABASE_POOL_TIMEOUT` | Seconds to wait for a pooled connection; default 10 |
| `ATHLETE_OIDC_ISSUER` | Exact HTTPS JWT issuer, including its trailing slash if present |
| `ATHLETE_OIDC_AUDIENCE` | Required token audience for this API |
| `ATHLETE_OIDC_JWKS_URL` | Configured HTTPS verification-key endpoint; never taken from token headers |
| `ATHLETE_OIDC_KEY_FORMAT` | `jwks` (default) for a JWKS document, or `x509` for a `{kid: PEM certificate}` map such as Firebase's |
| `ATHLETE_OIDC_REQUIRE_AUTH_TIME` | `true` to require an `auth_time` claim in the past (Firebase ID tokens); default `false` |
| `ATHLETE_EXTRACTION_PROVIDER` | `anthropic` to let the worker call the model provider; unset (default) keeps the worker idle. Requires the API key and a database URL |
| `ATHLETE_ANTHROPIC_API_KEY` | Anthropic API key held by the worker only; never logged or shown in settings output |
| `ATHLETE_EXTRACTION_MODEL` | Model identifier; default `claude-haiku-5-5`. It must have a price in `application/budget.py` |
| `ATHLETE_MODEL_BUDGET_USD_MONTHLY` | Monthly model budget in USD across providers, above 0 and at most 10000; default 25 |
| `ATHLETE_EXTRACTION_DAILY_ATTEMPTS_PER_ATHLETE` | Extraction attempts per athlete per UTC day, 1 to 10000; default 20 |

Supply TLS options in the PostgreSQL URL for remote databases. Keep pool limits small across processes. The runtime engine is lazy, and callers own its disposal. Use `build_session_factory(engine)` to retain loaded ORM attributes after commit and close; unloaded relationships still require an active session. Each application transaction gets a separate session that commits on success, rolls back on failure, and closes on exit. Never hold a transaction open during a model call.

Alembic uses the direct migration DSN without putting secrets into `alembic.ini`. The baseline is followed by migration `0002_profiles`, which adds users, owned athletes, and profile revisions. Do not use `Base.metadata.create_all()` to replace migrations.

For future model changes:

```bash
uv run --locked alembic revision --autogenerate -m "describe the schema change"
# Review the generated migration before applying it.
uv run --locked alembic upgrade head
uv run --locked alembic check
```

Import new persistence models into the migration metadata before generating revisions. Autogeneration is a starting point, not a substitute for review.

## Verification

Checks that do not need PostgreSQL:

```bash
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest -m "not integration"
```

Integration tests require an explicitly selected **disposable** PostgreSQL database. They exercise migration downgrade/upgrade and must never target a deployed or shared database. Before connecting, the test fixture requires a database name ending in `_test` and rejects a target matching the configured runtime host/port/database, including runtime configuration loaded from `.env`. This catches common mistakes; it cannot prove disposability or detect all host aliases. Use dedicated test credentials without access to deployed databases. Create the test database once after starting Compose:

```bash
docker compose exec postgres createdb -U athlete athlete_test
export TEST_DATABASE_URL='postgresql+psycopg://athlete:athlete@127.0.0.1:5433/athlete_test'
uv run --locked pytest
```

For subsequent runs, reuse the test database without rerunning `createdb`. Integration selection fails if `TEST_DATABASE_URL` is missing; it does not silently skip. Tests verify transaction rollback after a real PostgreSQL constraint failure, JSONB persistence, direct migration DSN selection, schema drift, and migration round trips.

GitHub Actions runs the same checks with an ephemeral PostgreSQL 17 service. No model or cloud credentials are needed.

## Athlete profile API

See [profile access and versioning](docs/engineering/profiles.md) for contracts, examples, and the authentication/RLS decisions. Authentication is fail-closed: without an issuer, audience, and JWKS endpoint, protected endpoints return 503 for a supplied token. Missing or invalid credentials return 401; health remains public and performs no database access.

The current adapter supports RS256-signed JWTs, validates signature, issuer, audience, expiry, issued-at, and subject, and retrieves keys only from the configured endpoint. Choosing a managed provider, token type/claims, and onboarding remains [issue #3](https://github.com/mcastrucci/athlete-platform/issues/3); this change does not select or provision one.

Profiles are created for the verified caller, not an athlete ID in a request body. Each full replacement requires an expected version, appends a revision, and rejects stale edits with 409. Another user's resource returns 404, including historical versions. Baselines and reported skill levels are self-reported context, not verified performance or automatic skill assessments.

## Language convention

All repository documentation, code, identifiers, comments, commit messages, and pull request titles and descriptions must be in English. See [AGENTS.md](AGENTS.md) for contributor instructions.

User-facing languages are a separate product decision. Localized content belongs in explicitly identified localization resources; internal documentation and domain identifiers remain in English.

## Documentation

- [Pilot PRD](docs/product/pilot-prd.md): user, scope, journey, acceptance criteria, and evaluation.
- [Pilot technical plan](docs/engineering/pilot-plan.md): architecture, data lifecycle, engine reuse, implementation stages, and validation.
- First coding task: [service foundation, issue #4](https://github.com/mcastrucci/athlete-platform/issues/4).
- Next: provider decisions in [#3](https://github.com/mcastrucci/athlete-platform/issues/3) and athlete ownership in [#5](https://github.com/mcastrucci/athlete-platform/issues/5).

Task status and dependencies live in GitHub Issues; the technical plan links the stages and implementation PRs.
