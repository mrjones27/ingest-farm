from __future__ import annotations

import logging

from redis import RedisError

from ingest_farm.common.events import (
    CHANNEL_CONNECT_QUEUE,
    CHANNEL_DISCONNECT_QUEUE,
    CHANNEL_ETR290_RESET_QUEUE,
    CHANNEL_RECORD_START_QUEUE,
    CHANNEL_RECORD_STOP_QUEUE,
    publish_event,
)
from ingest_farm.models import Channel, Worker
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class JobPublishError(RuntimeError):
    """The job could not be queued, so the state change was not accepted."""


class Scheduler:
    """Assigns channel connect/record jobs via Redis.

    Single-worker mode is the supported deployment: all jobs are broadcast on
    shared queues. ``worker_hint`` is not used for routing (farm scheduling is
    a future pass).
    """

    def _commit_then_publish(
        self,
        db: Session,
        channel: Channel,
        queue: str,
        payload: dict,
        new_status: str | None,
    ) -> None:
        """Record the intent before queueing, and undo it if queueing fails.

        Publishing first lets a fast worker write achieved state (``connected``,
        ``idle``) that the intent commit then overwrites, stranding the channel
        in a transitional status.
        """
        previous = channel.status
        if new_status is not None:
            channel.status = new_status
            db.commit()
        try:
            publish_event(queue, payload)
        except RedisError as exc:
            if new_status is not None:
                channel.status = previous
                db.commit()
            logger.error("Failed to queue %s for channel %s: %s", queue, channel.id, exc)
            raise JobPublishError(f"Could not queue job on {queue}") from exc

    def connect_channel(self, db: Session, channel_id: str) -> None:
        channel = db.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")
        if channel.status not in {"idle", "error"}:
            raise ValueError(f"Channel {channel_id} is already {channel.status}")

        worker = self._pick_worker(db)
        if worker is None:
            logger.warning("No workers registered yet; queueing connect for %s anyway", channel_id)

        # worker_hint is informational only (single-worker deployments).
        self._commit_then_publish(
            db,
            channel,
            CHANNEL_CONNECT_QUEUE,
            {"channel_id": channel_id, "worker_hint": worker.hostname if worker else None},
            "connecting",
        )
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

        self._commit_then_publish(
            db,
            channel,
            CHANNEL_DISCONNECT_QUEUE,
            {"channel_id": channel_id},
            "disconnecting",
        )
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

        # Only "connected" has an intent to record; connecting/starting already
        # carry one, so leave those statuses alone.
        self._commit_then_publish(
            db,
            channel,
            CHANNEL_RECORD_START_QUEUE,
            {"channel_id": channel_id},
            "starting" if channel.status == "connected" else None,
        )
        logger.info("Queued record start for channel %s", channel_id)

    def stop_recording(self, db: Session, channel_id: str) -> None:
        channel = db.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")
        if channel.status not in {"recording", "starting", "stopping"}:
            raise ValueError(f"Channel {channel_id} is not recording (status={channel.status})")

        self._commit_then_publish(
            db,
            channel,
            CHANNEL_RECORD_STOP_QUEUE,
            {"channel_id": channel_id},
            "stopping",
        )
        logger.info("Queued record stop for channel %s", channel_id)

    def reset_etr290(self, db: Session, channel_id: str) -> None:
        channel = db.get(Channel, channel_id)
        if channel is None:
            raise ValueError(f"Channel {channel_id} not found")
        if channel.protocol != "srt":
            raise ValueError("ETR 290 reset is only available for SRT channels")
        if channel.status not in {
            "connecting",
            "connected",
            "recording",
            "starting",
            "stopping",
        }:
            raise ValueError(f"Channel {channel_id} is not live (status={channel.status})")

        self._commit_then_publish(
            db,
            channel,
            CHANNEL_ETR290_RESET_QUEUE,
            {"channel_id": channel_id},
            None,
        )
        logger.info("Queued ETR 290 reset for channel %s", channel_id)

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
