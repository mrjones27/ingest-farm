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
        if channel.status in {"recording", "starting", "stopping"}:
            raise ValueError(f"Channel {channel_id} is already {channel.status}")

        worker = self._pick_worker(db)
        if worker is None:
            logger.warning("No workers registered yet; queueing start for %s anyway", channel_id)

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
        if channel.status not in {"recording", "starting"}:
            raise ValueError(f"Channel {channel_id} is not active (status={channel.status})")

        publish_event(CHANNEL_STOP_QUEUE, {"channel_id": channel_id})
        channel.status = "stopping"
        db.commit()
        logger.info("Queued stop for channel %s", channel_id)

    def _pick_worker(self, db: Session) -> Worker | None:
        workers = db.query(Worker).all()
        if not workers:
            return None
        return min(workers, key=lambda w: len(w.active_channels or []))
