"""Initial schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "channels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("protocol", sa.String(32), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("source_config", sa.JSON(), nullable=True),
        sa.Column("pipeline_profile", sa.JSON(), nullable=True),
        sa.Column("output_config", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(32), nullable=False, server_default="idle"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "workers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("hostname", sa.String(255), nullable=False, unique=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_channels", sa.JSON(), nullable=True),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="4"),
    )
    op.create_table(
        "recordings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("channel_id", sa.String(36), sa.ForeignKey("channels.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="recording"),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("segment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("recording_id", sa.String(36), sa.ForeignKey("recordings.id"), nullable=False, unique=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("video_codec", sa.String(64), nullable=True),
        sa.Column("audio_codec", sa.String(64), nullable=True),
        sa.Column("master_path", sa.Text(), nullable=False),
        sa.Column("proxy_path", sa.Text(), nullable=True),
        sa.Column("thumbnail_path", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("assets")
    op.drop_table("recordings")
    op.drop_table("workers")
    op.drop_table("channels")
