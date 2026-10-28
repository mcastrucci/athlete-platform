"""Establish the migration baseline without inventing product tables.

Revision ID: 0001_baseline
Revises: None
"""

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Alembic records the baseline; product schemas start with issue #5."""


def downgrade() -> None:
    """No product objects exist at this baseline."""
