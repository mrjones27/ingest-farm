"""Widen recordings.byte_size to BigInteger.

Revision ID: 0002_byte_size_bigint
Revises: 0001_initial
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_byte_size_bigint"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "recordings",
        "byte_size",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
        existing_server_default="0",
    )


def downgrade() -> None:
    op.alter_column(
        "recordings",
        "byte_size",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
        existing_server_default="0",
    )
