"""Add metadata JSON column to recordings.

Revision ID: 0003_recording_metadata
Revises: 0002_byte_size_bigint
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_recording_metadata"
down_revision: str | None = "0002_byte_size_bigint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recordings",
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("recordings", "metadata")
