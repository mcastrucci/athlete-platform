"""Add owned text sources and append-only interpretation revisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_sources"
down_revision: str | None = "0003_extraction_jobs"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("duplicate_of", sa.Uuid(), nullable=True),
        sa.Column("duplicate_status", sa.String(), nullable=False),
        sa.Column("interpretation_version", sa.Integer(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(duplicate_of IS NULL) = (duplicate_status = 'none')",
            name=op.f("ck_sources_duplicate_has_target"),
        ),
        sa.CheckConstraint(
            "duplicate_status IN ('none','pending','kept','discarded')",
            name=op.f("ck_sources_valid_duplicate"),
        ),
        sa.CheckConstraint("kind = 'text'", name=op.f("ck_sources_known_kind")),
        sa.CheckConstraint(
            "char_length(text) BETWEEN 1 AND 20000", name=op.f("ck_sources_bounded_text")
        ),
        sa.CheckConstraint(
            "interpretation_version IS NULL OR interpretation_version >= 1",
            name=op.f("ck_sources_positive_interpretation"),
        ),
        sa.ForeignKeyConstraint(
            ["athlete_id", "owner_user_id"],
            ["athletes.id", "athletes.owner_user_id"],
            name=op.f("fk_sources_athlete_id_athletes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of", "athlete_id"],
            ["sources.id", "sources.athlete_id"],
            name=op.f("fk_sources_duplicate_of_sources"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sources")),
        sa.UniqueConstraint("athlete_id", "idempotency_key", name=op.f("uq_sources_athlete_id")),
        sa.UniqueConstraint("id", "athlete_id", name=op.f("uq_sources_id")),
    )
    op.create_index("ix_sources_content", "sources", ["athlete_id", "content_hash"], unique=False)
    op.create_table(
        "interpretation_revisions",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("based_on_version", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(origin = 'extraction') = (run_id IS NOT NULL AND provenance IS NOT NULL)",
            name=op.f("ck_interpretation_revisions_extraction_has_provenance"),
        ),
        sa.CheckConstraint(
            "origin = 'extraction' OR based_on_version IS NOT NULL",
            name=op.f("ck_interpretation_revisions_correction_has_base"),
        ),
        sa.CheckConstraint(
            "origin IN ('extraction','correction')",
            name=op.f("ck_interpretation_revisions_valid_origin"),
        ),
        sa.CheckConstraint(
            "based_on_version IS NULL OR based_on_version < version",
            name=op.f("ck_interpretation_revisions_earlier_base"),
        ),
        sa.CheckConstraint(
            "schema_version = 1", name=op.f("ck_interpretation_revisions_known_schema")
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_interpretation_revisions_positive_version")
        ),
        sa.ForeignKeyConstraint(
            ["source_id", "athlete_id"],
            ["sources.id", "sources.athlete_id"],
            name=op.f("fk_interpretation_revisions_source_id_sources"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("source_id", "version", name=op.f("pk_interpretation_revisions")),
        sa.UniqueConstraint("run_id", name=op.f("uq_interpretation_revisions_run_id")),
    )


def downgrade() -> None:
    op.drop_table("interpretation_revisions")
    op.drop_index("ix_sources_content", table_name="sources")
    op.drop_table("sources")
