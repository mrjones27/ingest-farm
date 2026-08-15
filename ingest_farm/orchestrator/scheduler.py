from __future__ import annotations

import logging

from ingest_farm.common.events import CHANNEL_START_QUEUE, CHANNEL_STOP_QUEUE, publish_event
from ingest_farm.models import Channel, Worker
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class Scheduler:
    """Assigns channel start/stop jobs to the farm via Redis."""

    def start_channel(self, db: Session, channel_id: str) -> None:
        channel = db.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")
        if channel.status == "recording":
            raise ValueError(f"Channel {channel_id} is already recording")

        worker = self._pick_worker(db)
        publish_event(
            CHANNEL_START_QUEUE,
            {"channel_id": channel_id, "worker_hint": worker.hostname if worker else None},
        )
        channel.status = "starting"
        db.commit()
        logger.info("Queued start for channel %s", channel_id)

    def stop_channel(self, db: Session, channel_id: str) -> None:
        channel = db.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")

        publish_event(CHANNEL_STOP_QUEUE, {"channel_id": channel_id})
        channel.status = "stopping"
        db.commit()
        logger.info("Queued stop for channel %s", channel_id)

    def _pick_worker(self, db: Session) -> Worker | None:
        workers = db.query(Worker).all()
        if not workers:
            return None
        return min(workers, key=lambda w: len(w.active_channels or []))
