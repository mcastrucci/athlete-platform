"""Add extraction jobs, fenced attempts, and UTC quota counters."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_extraction_jobs"
down_revision: str | None = "0002_profiles"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "extraction_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("input_hash", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("attempt_limit", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_extraction_jobs"),
        sa.ForeignKeyConstraint(
            ["athlete_id", "owner_user_id"],
            ["athletes.id", "athletes.owner_user_id"],
            ondelete="CASCADE",
            name="fk_extraction_jobs_athlete_id_athletes",
        ),
        sa.UniqueConstraint("athlete_id", "idempotency_key", name="uq_extraction_jobs_athlete_id"),
        sa.UniqueConstraint("id", "athlete_id", name="uq_extraction_jobs_id"),
        sa.CheckConstraint(
            "state IN ('queued','running','deferred','succeeded','failed')",
            name=op.f("ck_extraction_jobs_valid_state"),
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempt_limit >= 1", name=op.f("ck_extraction_jobs_valid_attempts")
        ),
        sa.CheckConstraint(
            "state != 'running' OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name=op.f("ck_extraction_jobs_running_has_lease"),
        ),
    )
    op.create_index("ix_extraction_jobs_claim", "extraction_jobs", ["state", "available_at"])
    op.create_table(
        "job_attempts",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("job_id", "number", name="pk_job_attempts"),
        sa.ForeignKeyConstraint(
            ["job_id", "athlete_id"],
            ["extraction_jobs.id", "extraction_jobs.athlete_id"],
            ondelete="CASCADE",
            name="fk_job_attempts_job_id_extraction_jobs",
        ),
        sa.UniqueConstraint("lease_token", name="uq_job_attempts_lease_token"),
        sa.CheckConstraint("number >= 1", name=op.f("ck_job_attempts_positive_number")),
        sa.CheckConstraint(
            "outcome IN ('running','succeeded','failed','expired')",
            name=op.f("ck_job_attempts_valid_outcome"),
        ),
    )
    op.create_table(
        "daily_job_attempts",
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("athlete_id", "day", name="pk_daily_job_attempts"),
        sa.ForeignKeyConstraint(
            ["athlete_id"],
            ["athletes.id"],
            ondelete="CASCADE",
            name="fk_daily_job_attempts_athlete_id_athletes",
        ),
        sa.CheckConstraint("count >= 0", name=op.f("ck_daily_job_attempts_nonnegative_count")),
    )


def downgrade() -> None:
    op.drop_table("daily_job_attempts")
    op.drop_table("job_attempts")
    op.drop_index("ix_extraction_jobs_claim", table_name="extraction_jobs")
    op.drop_table("extraction_jobs")
