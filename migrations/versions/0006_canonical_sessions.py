"""Add confirmations and the canonical sessions and items they materialize."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_canonical_sessions"
down_revision: str | None = "0005_model_budget"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "source_confirmations",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("interpretation_version", sa.Integer(), nullable=False),
        sa.Column(
            "confirmed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "athlete_id"],
            ["sources.id", "sources.athlete_id"],
            name=op.f("fk_source_confirmations_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "interpretation_version"],
            ["interpretation_revisions.source_id", "interpretation_revisions.version"],
            name=op.f("fk_source_confirmations_source_id_interpretation_revisions"),
        ),
        sa.PrimaryKeyConstraint("source_id", name=op.f("pk_source_confirmations")),
        sa.UniqueConstraint(
            "source_id", "athlete_id", name=op.f("uq_source_confirmations_source_id")
        ),
    )
    op.create_table(
        "canonical_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("date_text", sa.String(), nullable=True),
        sa.Column("duration_value", sa.Float(), nullable=True),
        sa.Column("duration_unit", sa.String(), nullable=True),
        sa.Column("session_rpe", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("ambiguities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned','performed')", name=op.f("ck_canonical_sessions_valid_status")
        ),
        sa.CheckConstraint(
            "(duration_value IS NULL) = (duration_unit IS NULL)",
            name=op.f("ck_canonical_sessions_duration_has_unit"),
        ),
        sa.CheckConstraint(
            "duration_value IS NULL OR duration_value >= 0",
            name=op.f("ck_canonical_sessions_valid_duration"),
        ),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_canonical_sessions_nonnegative_position")
        ),
        sa.CheckConstraint(
            "session_rpe IS NULL OR session_rpe BETWEEN 0 AND 10",
            name=op.f("ck_canonical_sessions_valid_session_rpe"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "athlete_id"],
            ["source_confirmations.source_id", "source_confirmations.athlete_id"],
            name=op.f("fk_canonical_sessions_source_id_source_confirmations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canonical_sessions")),
        sa.UniqueConstraint("source_id", "position", name=op.f("uq_canonical_sessions_source_id")),
    )
    op.create_index(
        "ix_canonical_sessions_athlete_date",
        "canonical_sessions",
        ["athlete_id", "session_date"],
        unique=False,
    )
    op.create_table(
        "canonical_session_items",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("exercise_text", sa.String(), nullable=False),
        sa.Column("exercise_id", sa.String(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("prescription_text", sa.String(), nullable=True),
        sa.Column("sets", sa.Integer(), nullable=True),
        sa.Column("reps", sa.Integer(), nullable=True),
        sa.Column("rounds", sa.Integer(), nullable=True),
        sa.Column("load_value", sa.Float(), nullable=True),
        sa.Column("load_unit", sa.String(), nullable=True),
        sa.Column("duration_value", sa.Float(), nullable=True),
        sa.Column("duration_unit", sa.String(), nullable=True),
        sa.Column("distance_value", sa.Float(), nullable=True),
        sa.Column("distance_unit", sa.String(), nullable=True),
        sa.Column("rpe", sa.Float(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("ambiguities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "kind IN ('strength','conditioning','skill','mobility','other','unknown')",
            name=op.f("ck_canonical_session_items_valid_kind"),
        ),
        sa.CheckConstraint(
            "(load_value IS NULL OR load_value >= 0) "
            "AND (duration_value IS NULL OR duration_value >= 0) "
            "AND (distance_value IS NULL OR distance_value >= 0)",
            name=op.f("ck_canonical_session_items_nonnegative_quantities"),
        ),
        sa.CheckConstraint(
            "(load_value IS NULL) = (load_unit IS NULL) "
            "AND (duration_value IS NULL) = (duration_unit IS NULL) "
            "AND (distance_value IS NULL) = (distance_unit IS NULL)",
            name=op.f("ck_canonical_session_items_quantities_have_units"),
        ),
        sa.CheckConstraint(
            "(sets IS NULL OR sets >= 0) AND (reps IS NULL OR reps >= 0) "
            "AND (rounds IS NULL OR rounds >= 0)",
            name=op.f("ck_canonical_session_items_nonnegative_counts"),
        ),
        sa.CheckConstraint(
            "position >= 0", name=op.f("ck_canonical_session_items_nonnegative_position")
        ),
        sa.CheckConstraint(
            "rpe IS NULL OR rpe BETWEEN 0 AND 10", name=op.f("ck_canonical_session_items_valid_rpe")
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["canonical_sessions.id"],
            name=op.f("fk_canonical_session_items_session_id_canonical_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("session_id", "position", name=op.f("pk_canonical_session_items")),
    )


def downgrade() -> None:
    op.drop_table("canonical_session_items")
    op.drop_index("ix_canonical_sessions_athlete_date", table_name="canonical_sessions")
    op.drop_table("canonical_sessions")
    op.drop_table("source_confirmations")
