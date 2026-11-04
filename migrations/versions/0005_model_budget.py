"""Add the monthly model-spend ledger and its per-month lock rows."""

import sqlalchemy as sa
from alembic import op

revision: str = "0005_model_budget"
down_revision: str | None = "0004_sources"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "model_budget_months",
        sa.Column("month", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("month", name=op.f("pk_model_budget_months")),
        sa.CheckConstraint(
            "extract(day FROM month) = 1", name=op.f("ck_model_budget_months_first_of_month")
        ),
    )
    op.create_table(
        "model_spend",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("reserved_usd", sa.Numeric(14, 6), nullable=False),
        sa.Column("spent_usd", sa.Numeric(14, 6), nullable=False),
        sa.Column("uncertain_usd", sa.Numeric(14, 6), server_default="0", nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("job_id", "month", name=op.f("pk_model_spend")),
        sa.ForeignKeyConstraint(
            ["month"],
            ["model_budget_months.month"],
            name=op.f("fk_model_spend_month_model_budget_months"),
        ),
        sa.CheckConstraint(
            "reserved_usd >= 0 AND spent_usd >= 0 AND uncertain_usd >= 0",
            name=op.f("ck_model_spend_nonnegative_amounts"),
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0",
            name=op.f("ck_model_spend_nonnegative_tokens"),
        ),
    )
    op.create_index("ix_model_spend_month", "model_spend", ["month"])


def downgrade() -> None:
    op.drop_index("ix_model_spend_month", table_name="model_spend")
    op.drop_table("model_spend")
    op.drop_table("model_budget_months")
