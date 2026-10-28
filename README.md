# athlete-platform

A product to help athletes who already train organize their development: clear goals, volume, periodization, progression, and explained decisions.

Internal working name. The commercial brand is still undecided.

This repository contains product definition, decisions, and the initial Python service foundation. Training workflows, authentication, and deployment are not implemented yet.

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

The worker is deliberately a bootstrap: it logs that no handlers exist and exits successfully without polling or opening a database connection. Durable jobs are tracked in [issue #6](https://github.com/mcastrucci/athlete-platform/issues/6).

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

Supply TLS options in the PostgreSQL URL for remote databases. Keep pool limits small across processes. The runtime engine is lazy, and callers own its disposal. Use `build_session_factory(engine)` to retain loaded ORM attributes after commit and close; unloaded relationships still require an active session. Each application transaction gets a separate session that commits on success, rolls back on failure, and closes on exit. Never hold a transaction open during a model call.

Alembic uses the direct migration DSN without putting secrets into `alembic.ini`. The initial migration establishes only the revision baseline; athlete/product tables belong to [issue #5](https://github.com/mcastrucci/athlete-platform/issues/5). Do not use `Base.metadata.create_all()` to replace migrations.

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

## Language convention

All repository documentation, code, identifiers, comments, commit messages, and pull request titles and descriptions must be in English. See [AGENTS.md](AGENTS.md) for contributor instructions.

User-facing languages are a separate product decision. Localized content belongs in explicitly identified localization resources; internal documentation and domain identifiers remain in English.

## Documentation

- [Pilot PRD](docs/product/pilot-prd.md): user, scope, journey, acceptance criteria, and evaluation.
- [Pilot technical plan](docs/engineering/pilot-plan.md): architecture, data lifecycle, engine reuse, implementation stages, and validation.
- First coding task: [service foundation, issue #4](https://github.com/mcastrucci/athlete-platform/issues/4).
- Next: provider decisions in [#3](https://github.com/mcastrucci/athlete-platform/issues/3) and athlete ownership in [#5](https://github.com/mcastrucci/athlete-platform/issues/5).

Task status and dependencies live in GitHub Issues; the technical plan links the stages and implementation PRs.
