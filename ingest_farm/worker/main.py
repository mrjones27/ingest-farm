from __future__ import annotations

import logging
import signal
from datetime import datetime, timezone

from ingest_farm.common.events import (
    CHANNEL_CONNECT_QUEUE,
    CHANNEL_DISCONNECT_QUEUE,
    CHANNEL_RECORD_START_QUEUE,
    CHANNEL_RECORD_STOP_QUEUE,
    POSTPROCESS_QUEUE,
    RECORDING_COMPLETE_QUEUE,
    blocking_pop,
    clear_channel_stats,
    publish_event,
    set_channel_stats,
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
from ingest_farm.worker.recorder import ChannelSession

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = (
    "connecting",
    "connected",
    "recording",
    "disconnecting",
    "starting",
    "stopping",
)


class IngestWorker:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.worker_id = self.settings.resolved_worker_id
        self.storage = LocalStorage()
        self._sessions: dict[str, ChannelSession] = {}
        self._recording_ids: dict[str, str] = {}
        self._pending_record: set[str] = set()
        self._running = True
        self._loop = None

    def run(self) -> None:
        import threading

        from ingest_farm.common.gst_utils import init_gstreamer
        from gi.repository import GLib

        init_db()
        init_gstreamer()
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)
        logger.info("Worker %s starting (capacity=%s)", self.worker_id, self.settings.worker_capacity)
        self._register_worker()
        self._reconcile_orphaned_channels()
        self._loop = GLib.MainLoop()

        def _connect_jobs() -> None:
            while self._running:
                job = blocking_pop(CHANNEL_CONNECT_QUEUE, timeout=1)
                if job:
                    GLib.idle_add(self._idle_connect, job)

        threading.Thread(target=_connect_jobs, daemon=True, name="connect-jobs").start()
        GLib.timeout_add_seconds(1, self._glib_tick)
        self._loop.run()

    def _idle_connect(self, job: dict) -> bool:
        try:
            self._handle_connect(job)
        except Exception:
            logger.exception("idle connect failed")
        return False

    def _glib_tick(self) -> bool:
        if not self._running:
            if self._loop is not None and self._loop.is_running():
                self._loop.quit()
            return False
        try:
            self._heartbeat()
            self._drain_control_queues()
        except Exception:
            logger.exception("worker tick failed")
        return True

    def _drain_control_queues(self) -> None:
        for queue, handler in (
            (CHANNEL_DISCONNECT_QUEUE, self._handle_disconnect),
            (CHANNEL_RECORD_STOP_QUEUE, self._handle_record_stop),
            (CHANNEL_RECORD_START_QUEUE, self._handle_record_start),
        ):
            job = try_pop(queue)
            while job:
                handler(job)
                job = try_pop(queue)

    def _register_worker(self) -> None:
        with get_session_factory()() as db:
            worker = db.query(Worker).filter_by(hostname=self.worker_id).one_or_none()
            if worker is None:
                worker = Worker(hostname=self.worker_id, capacity=self.settings.worker_capacity)
                db.add(worker)
            worker.last_heartbeat = datetime.now(timezone.utc)
            worker.active_channels = list(self._sessions.keys())
            db.commit()

    def _reconcile_orphaned_channels(self) -> None:
        with get_session_factory()() as db:
            orphans = db.query(Channel).filter(Channel.status.in_(ACTIVE_STATUSES)).all()
            cleared: list[str] = []
            for channel in orphans:
                if channel.id in self._sessions:
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
                worker.active_channels = list(self._sessions.keys())

            for channel_id, session in list(self._sessions.items()):
                if session.is_connected:
                    stats = session.get_stats()
                    if stats:
                        try:
                            set_channel_stats(channel_id, stats, ttl_sec=10)
                        except Exception:
                            logger.debug("Failed to publish stats for %s", channel_id, exc_info=True)
                    continue
                self._sessions.pop(channel_id, None)
                self._recording_ids.pop(channel_id, None)
                self._pending_record.discard(channel_id)
                clear_channel_stats(channel_id)
                ch = db.get(Channel, channel_id)
                if ch and ch.status in ACTIVE_STATUSES:
                    ch.status = "error"
                    logger.warning("Channel %s session ended unexpectedly → error", channel_id)
            db.commit()

    def _handle_connect(self, job: dict) -> None:
        channel_id = job["channel_id"]
        if channel_id in self._sessions and self._sessions[channel_id].is_connected:
            logger.warning("Channel %s already connected", channel_id)
            with get_session_factory()() as db:
                ch = db.get(Channel, channel_id)
                if ch and ch.status == "connecting":
                    ch.status = "connected"
                    db.commit()
            if channel_id in self._pending_record:
                self._handle_record_start({"channel_id": channel_id})
            return

        if len(self._sessions) >= self.settings.worker_capacity:
            logger.warning("Worker at capacity, rejecting channel %s", channel_id)
            with get_session_factory()() as db:
                ch = db.get(Channel, channel_id)
                if ch and ch.status in {"connecting", "starting"}:
                    ch.status = "error"
                    db.commit()
            return

        with get_session_factory()() as db:
            channel = db.get(Channel, channel_id)
            if channel is None:
                logger.error("Channel %s not found", channel_id)
                return
            config = self._channel_to_config(channel)
            start_record_after = channel.status == "starting"

        session = ChannelSession()
        preview_dir = self.storage.root / channel_id / "live"
        try:
            # #region agent log
            from ingest_farm.common.agent_debug import agent_log

            agent_log(
                "A",
                "worker/main.py:_handle_connect",
                "connect requested",
                {
                    "channel_id": channel_id,
                    "name": config.name,
                    "uri": config.source.uri,
                    "protocol": config.source.protocol,
                },
                run_id="obs-caller",
            )
            # #endregion
            session.connect(config, preview_dir)
            self._sessions[channel_id] = session
            with get_session_factory()() as db:
                ch = db.get(Channel, channel_id)
                if ch and ch.status in {"connecting", "starting"}:
                    # Keep "starting" if a record was requested; otherwise connected.
                    if ch.status == "connecting":
                        ch.status = "connected"
                    db.commit()
            logger.info("Connected channel %s", channel_id)
            if start_record_after or channel_id in self._pending_record:
                self._handle_record_start({"channel_id": channel_id})
        except Exception:
            logger.exception("Failed to connect channel %s", channel_id)
            with get_session_factory()() as db:
                ch = db.get(Channel, channel_id)
                if ch:
                    ch.status = "error"
                    db.commit()

    def _handle_disconnect(self, job: dict) -> None:
        channel_id = job["channel_id"]
        self._pending_record.discard(channel_id)
        session = self._sessions.pop(channel_id, None)
        if session is not None:
            if session.is_recording:
                self._finalize_recording(channel_id, session, completed=True)
            session.disconnect()
        clear_channel_stats(channel_id)
        with get_session_factory()() as db:
            channel = db.get(Channel, channel_id)
            if channel and channel.status in ACTIVE_STATUSES:
                channel.status = "idle"
                db.commit()
                logger.info("Disconnected channel %s → idle", channel_id)

    def _handle_record_start(self, job: dict) -> None:
        channel_id = job["channel_id"]
        session = self._sessions.get(channel_id)
        if session is None or not session.is_connected:
            self._pending_record.add(channel_id)
            logger.info("Record start pending until channel %s connects", channel_id)
            return
        if session.is_recording:
            self._pending_record.discard(channel_id)
            return

        self._pending_record.discard(channel_id)
        output_dir = self.storage.session_dir(channel_id, session.session_id)
        with get_session_factory()() as db:
            recording = Recording(
                channel_id=channel_id,
                storage_path=str(output_dir),
                status=RecordingStatus.RECORDING.value,
            )
            db.add(recording)
            channel = db.get(Channel, channel_id)
            if channel:
                channel.status = "recording"
            db.commit()
            self._recording_ids[channel_id] = recording.id

        try:
            session.start_recording(output_dir)
            logger.info("Recording started for channel %s", channel_id)
        except Exception:
            logger.exception("Failed to start recording for channel %s", channel_id)
            with get_session_factory()() as db:
                rid = self._recording_ids.pop(channel_id, None)
                if rid:
                    rec = db.get(Recording, rid)
                    if rec:
                        rec.status = RecordingStatus.FAILED.value
                        rec.ended_at = datetime.now(timezone.utc)
                ch = db.get(Channel, channel_id)
                if ch:
                    ch.status = "connected" if session.is_connected else "error"
                db.commit()

    def _handle_record_stop(self, job: dict) -> None:
        channel_id = job["channel_id"]
        self._pending_record.discard(channel_id)
        session = self._sessions.get(channel_id)
        if session is None:
            with get_session_factory()() as db:
                channel = db.get(Channel, channel_id)
                if channel and channel.status in {"stopping", "recording", "starting"}:
                    channel.status = "idle"
                    db.commit()
            return

        self._finalize_recording(channel_id, session, completed=True)
        with get_session_factory()() as db:
            channel = db.get(Channel, channel_id)
            if channel:
                channel.status = "connected" if session.is_connected else "idle"
                db.commit()

    def _finalize_recording(
        self, channel_id: str, session: ChannelSession, *, completed: bool
    ) -> None:
        if not session.is_recording and channel_id not in self._recording_ids:
            return
        if session.is_recording:
            session.stop_recording()
        output_dir = session.output_dir
        segment_count = session.segment_count
        byte_size = self.storage.total_size(output_dir) if output_dir else 0
        recording_id = self._recording_ids.pop(channel_id, None)

        with get_session_factory()() as db:
            recording = None
            if recording_id:
                recording = db.get(Recording, recording_id)
            if recording is None:
                recording = (
                    db.query(Recording)
                    .filter_by(channel_id=channel_id, status=RecordingStatus.RECORDING.value)
                    .order_by(Recording.started_at.desc())
                    .first()
                )
            if recording is None:
                return
            recording.status = (
                RecordingStatus.COMPLETED.value if completed else RecordingStatus.FAILED.value
            )
            recording.ended_at = datetime.now(timezone.utc)
            recording.segment_count = segment_count
            recording.byte_size = byte_size
            db.commit()
            if completed:
                publish_event(
                    RECORDING_COMPLETE_QUEUE,
                    {"recording_id": recording.id, "channel_id": channel_id},
                )
                publish_event(
                    POSTPROCESS_QUEUE,
                    {"recording_id": recording.id, "channel_id": channel_id},
                )

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
        from gi.repository import GLib

        logger.info("Shutting down worker")
        self._running = False
        for channel_id in list(self._sessions):
            self._handle_disconnect({"channel_id": channel_id})
        if self._loop is not None and self._loop.is_running():
            GLib.idle_add(self._loop.quit)
