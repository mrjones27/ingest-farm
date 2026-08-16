from __future__ import annotations

import logging

from ingest_farm.common.events import (
    CHANNEL_CONNECT_QUEUE,
    CHANNEL_DISCONNECT_QUEUE,
    CHANNEL_RECORD_START_QUEUE,
    CHANNEL_RECORD_STOP_QUEUE,
    publish_event,
)
from ingest_farm.models import Channel, Worker
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class Scheduler:
    """Assigns channel connect/record jobs to the farm via Redis."""

    def connect_channel(self, db: Session, channel_id: str) -> None:
        channel = db.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")
        if channel.status not in {"idle", "error"}:
            raise ValueError(f"Channel {channel_id} is already {channel.status}")

        worker = self._pick_worker(db)
        if worker is None:
            logger.warning("No workers registered yet; queueing connect for %s anyway", channel_id)

        publish_event(
            CHANNEL_CONNECT_QUEUE,
            {"channel_id": channel_id, "worker_hint": worker.hostname if worker else None},
        )
        channel.status = "connecting"
        db.commit()
        logger.info("Queued connect for channel %s", channel_id)

    def disconnect_channel(self, db: Session, channel_id: str) -> None:
        channel = db.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")
        if channel.status not in {
            "connecting",
            "connected",
            "recording",
            "disconnecting",
            "starting",
            "stopping",
        }:
            raise ValueError(f"Channel {channel_id} is not active (status={channel.status})")

        publish_event(CHANNEL_DISCONNECT_QUEUE, {"channel_id": channel_id})
        channel.status = "disconnecting"
        db.commit()
        logger.info("Queued disconnect for channel %s", channel_id)

    def start_recording(self, db: Session, channel_id: str) -> None:
        channel = db.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")
        # Allow idle→record via auto-connect for legacy /start callers.
        if channel.status == "idle":
            self.connect_channel(db, channel_id)
            db.refresh(channel)
        if channel.status not in {"connected", "connecting", "starting"}:
            raise ValueError(
                f"Channel {channel_id} must be connected before recording (status={channel.status})"
            )

        publish_event(CHANNEL_RECORD_START_QUEUE, {"channel_id": channel_id})
        if channel.status == "connected":
            channel.status = "starting"
            db.commit()
        logger.info("Queued record start for channel %s", channel_id)

    def stop_recording(self, db: Session, channel_id: str) -> None:
        channel = db.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")
        if channel.status not in {"recording", "starting", "stopping"}:
            raise ValueError(f"Channel {channel_id} is not recording (status={channel.status})")

        publish_event(CHANNEL_RECORD_STOP_QUEUE, {"channel_id": channel_id})
        channel.status = "stopping"
        db.commit()
        logger.info("Queued record stop for channel %s", channel_id)

    # Back-compat names used by older API routes.
    def start_channel(self, db: Session, channel_id: str) -> None:
        self.start_recording(db, channel_id)

    def stop_channel(self, db: Session, channel_id: str) -> None:
        self.stop_recording(db, channel_id)

    def _pick_worker(self, db: Session) -> Worker | None:
        workers = db.query(Worker).all()
        if not workers:
            return None
        return min(workers, key=lambda w: len(w.active_channels or []))
