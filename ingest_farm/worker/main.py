from __future__ import annotations

import logging
import signal
from datetime import datetime, timezone

from ingest_farm.common.events import (
    CHANNEL_START_QUEUE,
    CHANNEL_STOP_QUEUE,
    POSTPROCESS_QUEUE,
    RECORDING_COMPLETE_QUEUE,
    blocking_pop,
    publish_event,
    try_pop,
)
from ingest_farm.common.storage import LocalStorage
from ingest_farm.config import get_settings
from ingest_farm.db import get_session_factory, init_db
from ingest_farm.models import Channel, Recording, Worker
from ingest_farm.schemas import (
    ChannelConfig,
    OutputConfig,
    PipelineConfig,
    RecordingStatus,
    SourceConfig,
    SourceProtocol,
)
from ingest_farm.worker.recorder import PipelineRecorder

logger = logging.getLogger(__name__)


class IngestWorker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.worker_id = self.settings.resolved_worker_id
        self.storage = LocalStorage()
        self._recorders: dict[str, PipelineRecorder] = {}
        self._running = True

    def run(self) -> None:
        init_db()
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)
        logger.info("Worker %s starting (capacity=%s)", self.worker_id, self.settings.worker_capacity)
        self._register_worker()
        self._reconcile_orphaned_channels()

        while self._running:
            self._heartbeat()
            # Prefer draining stop requests so channels can end promptly.
            stop_job = try_pop(CHANNEL_STOP_QUEUE)
            while stop_job:
                self._handle_stop(stop_job)
                stop_job = try_pop(CHANNEL_STOP_QUEUE)

            job = blocking_pop(CHANNEL_START_QUEUE, timeout=1)
            if job:
                self._handle_start(job)

    def _register_worker(self) -> None:
        with get_session_factory()() as db:
            worker = db.query(Worker).filter_by(hostname=self.worker_id).one_or_none()
            if worker is None:
                worker = Worker(hostname=self.worker_id, capacity=self.settings.worker_capacity)
                db.add(worker)
            worker.last_heartbeat = datetime.now(timezone.utc)
            worker.active_channels = list(self._recorders.keys())
            db.commit()

    def _reconcile_orphaned_channels(self) -> None:
        """Clear channels left in starting/recording/stopping after a worker crash."""
        with get_session_factory()() as db:
            orphans = (
                db.query(Channel)
                .filter(Channel.status.in_(("starting", "recording", "stopping")))
                .all()
            )
            cleared: list[str] = []
            for channel in orphans:
                if channel.id in self._recorders:
                    continue
                old = channel.status
                channel.status = "idle"
                cleared.append(f"{channel.name}:{old}")
                recording = (
                    db.query(Recording)
                    .filter_by(channel_id=channel.id, status=RecordingStatus.RECORDING.value)
                    .order_by(Recording.started_at.desc())
                    .first()
                )
                if recording:
                    recording.status = RecordingStatus.FAILED.value
                    recording.ended_at = datetime.now(timezone.utc)
            if cleared:
                db.commit()
                logger.warning("Reconciled orphaned channels → idle: %s", cleared)

    def _heartbeat(self) -> None:
        with get_session_factory()() as db:
            worker = db.query(Worker).filter_by(hostname=self.worker_id).one_or_none()
            if worker:
                worker.last_heartbeat = datetime.now(timezone.utc)
                worker.active_channels = list(self._recorders.keys())
                db.commit()

    def _handle_start(self, job: dict) -> None:
        channel_id = job["channel_id"]
        if channel_id in self._recorders:
            logger.warning("Channel %s already recording on this worker", channel_id)
            return
        if len(self._recorders) >= self.settings.worker_capacity:
            logger.warning("Worker at capacity, rejecting channel %s", channel_id)
            with get_session_factory()() as db:
                ch = db.get(Channel, channel_id)
                if ch and ch.status == "starting":
                    ch.status = "error"
                    db.commit()
            return

        with get_session_factory()() as db:
            channel = db.get(Channel, channel_id)
            if channel is None:
                logger.error("Channel %s not found", channel_id)
                return

            config = self._channel_to_config(channel)
            recorder = PipelineRecorder()
            output_dir = self.storage.session_dir(channel_id, recorder.session_id)

            recording = Recording(
                channel_id=channel_id,
                storage_path=str(output_dir),
                status=RecordingStatus.RECORDING.value,
            )
            db.add(recording)
            channel.status = "recording"
            db.commit()
            recording_id = recording.id

        try:
            recorder.start(config, output_dir)
            self._recorders[channel_id] = recorder
            logger.info("Started recording %s for channel %s", recording_id, channel_id)
        except Exception:
            logger.exception("Failed to start pipeline for channel %s", channel_id)
            with get_session_factory()() as db:
                rec = db.get(Recording, recording_id)
                if rec:
                    rec.status = RecordingStatus.FAILED.value
                    rec.ended_at = datetime.now(timezone.utc)
                ch = db.get(Channel, channel_id)
                if ch:
                    ch.status = "error"
                db.commit()

    def _handle_stop(self, job: dict) -> None:
        channel_id = job["channel_id"]
        recorder = self._recorders.pop(channel_id, None)
        if recorder is None:
            # Worker crash / orphaned stop job — still clear sticky "stopping".
            with get_session_factory()() as db:
                channel = db.get(Channel, channel_id)
                if channel and channel.status in {"stopping", "starting", "recording"}:
                    channel.status = "idle"
                    recording = (
                        db.query(Recording)
                        .filter_by(channel_id=channel_id, status=RecordingStatus.RECORDING.value)
                        .order_by(Recording.started_at.desc())
                        .first()
                    )
                    if recording:
                        recording.status = RecordingStatus.FAILED.value
                        recording.ended_at = datetime.now(timezone.utc)
                    db.commit()
                    logger.warning(
                        "Cleared orphaned channel %s → idle (no local recorder)", channel_id
                    )
            return

        recorder.stop()
        output_dir = recorder.output_dir
        segment_count = recorder.segment_count
        byte_size = self.storage.total_size(output_dir) if output_dir else 0

        with get_session_factory()() as db:
            channel = db.get(Channel, channel_id)
            if channel:
                channel.status = "idle"
            recording = (
                db.query(Recording)
                .filter_by(channel_id=channel_id, status=RecordingStatus.RECORDING.value)
                .order_by(Recording.started_at.desc())
                .first()
            )
            if recording:
                recording.status = RecordingStatus.COMPLETED.value
                recording.ended_at = datetime.now(timezone.utc)
                recording.segment_count = segment_count
                recording.byte_size = byte_size
                db.commit()
                publish_event(
                    RECORDING_COMPLETE_QUEUE,
                    {"recording_id": recording.id, "channel_id": channel_id},
                )
                publish_event(
                    POSTPROCESS_QUEUE,
                    {"recording_id": recording.id, "channel_id": channel_id},
                )
            else:
                db.commit()

    def _channel_to_config(self, channel: Channel) -> ChannelConfig:
        profile = channel.pipeline_profile or {}
        output = channel.output_config or {}
        return ChannelConfig(
            name=channel.name,
            source=SourceConfig(
                protocol=SourceProtocol(channel.protocol),
                uri=channel.source_uri,
                config=channel.source_config or {},
            ),
            pipeline=PipelineConfig(**profile),
            output=OutputConfig(**output),
        )

    def _shutdown(self, *_args) -> None:
        logger.info("Shutting down worker")
        self._running = False
        for channel_id in list(self._recorders):
            self._handle_stop({"channel_id": channel_id})
