"""Add stored pull-budget evaluations keyed by athlete, week and manifest hash."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_evaluations"
down_revision: str | None = "0006_canonical_sessions"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("week", sa.String(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest_hash", sa.String(), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assumptions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("week ~ '^[0-9]{4}-W[0-9]{2}$'", name=op.f("ck_evaluations_valid_week")),
        sa.CheckConstraint(
            "manifest_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_evaluations_valid_manifest_hash")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(manifest) = 'object'", name=op.f("ck_evaluations_manifest_is_object")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(findings) = 'array'", name=op.f("ck_evaluations_findings_is_array")
        ),
        sa.CheckConstraint(
            "jsonb_typeof(assumptions) = 'array'", name=op.f("ck_evaluations_assumptions_is_array")
        ),
        sa.ForeignKeyConstraint(
            ["athlete_id"], ["athletes.id"], name=op.f("fk_evaluations_athlete_id_athletes")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluations")),
        sa.UniqueConstraint(
            "athlete_id", "week", "manifest_hash", name=op.f("uq_evaluations_athlete_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("evaluations")
