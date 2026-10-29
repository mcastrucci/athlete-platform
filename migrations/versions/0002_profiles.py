"""Add owned athletes and immutable application-level profile revisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_profiles"
down_revision: str | None = "0001_baseline"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("issuer", "subject", name="uq_users_issuer"),
    )
    op.create_table(
        "athletes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_athletes"),
        sa.ForeignKeyConstraint(
            ["owner_user_id"], ["users.id"], name="fk_athletes_owner_user_id_users"
        ),
        sa.UniqueConstraint("owner_user_id", name="uq_athletes_owner_user_id"),
        sa.UniqueConstraint("id", "owner_user_id", name="uq_athletes_id"),
        sa.CheckConstraint("version >= 1", name=op.f("ck_athletes_positive_version")),
    )
    op.create_table(
        "profile_revisions",
        sa.Column("athlete_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("athlete_id", "version", name="pk_profile_revisions"),
        sa.ForeignKeyConstraint(
            ["athlete_id", "owner_user_id"],
            ["athletes.id", "athletes.owner_user_id"],
            name="fk_profile_revisions_athlete_id_athletes",
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_profile_revisions_positive_version")),
        sa.CheckConstraint("schema_version = 1", name=op.f("ck_profile_revisions_known_schema")),
    )


def downgrade() -> None:
    op.drop_table("profile_revisions")
    op.drop_table("athletes")
    op.drop_table("users")
