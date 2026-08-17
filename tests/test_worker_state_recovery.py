"""Crash recovery and control-job ordering in the ingest worker.

Desired state lives in Postgres and runtime state lives in GStreamer, so every
case here is about the worker reconciling the two without stranding a channel
or a recording in a status nothing will ever clear.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from redis import RedisError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ingest_farm.models import Base, Channel, Recording, Worker
from ingest_farm.schemas import new_id
from ingest_farm.worker.main import IngestWorker


@pytest.fixture
def worker_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    class _Settings:
        database_url = "sqlite://"
        redis_url = "redis://localhost:6379/0"
        storage_root = tmp_path
        worker_id = "worker-1"
        worker_capacity = 4
        resolved_worker_id = "worker-1"

    monkeypatch.setattr("ingest_farm.worker.main.get_settings", lambda: _Settings())
    monkeypatch.setattr("ingest_farm.common.storage.get_settings", lambda: _Settings())
    monkeypatch.setattr("ingest_farm.worker.main.get_session_factory", lambda: factory)
    monkeypatch.setattr("ingest_farm.worker.main.clear_channel_stats", lambda channel_id: None)

    worker = IngestWorker()
    worker._sessions = {}
    return worker, factory


def _seed(factory, *, status: str, claimed: bool = True, recording: str | None = "recording"):
    with factory() as db:
        channel = Channel(
            id=new_id(),
            name="ch",
            protocol="udp",
            source_uri="udp://0.0.0.0:5000",
            status=status,
        )
        db.add(channel)
        db.add(
            Worker(
                hostname="worker-1",
                capacity=4,
                active_channels=[channel.id] if claimed else [],
                last_heartbeat=datetime.now(timezone.utc),
            )
        )
        if recording is not None:
            db.add(
                Recording(
                    id=new_id(),
                    channel_id=channel.id,
                    storage_path="/tmp/session",
                    status=recording,
                )
            )
        db.commit()
        return channel.id


def _state(factory, channel_id: str) -> tuple[str, list[str]]:
    with factory() as db:
        channel = db.get(Channel, channel_id)
        statuses = [r.status for r in db.query(Recording).filter_by(channel_id=channel_id).all()]
        return channel.status, statuses


class _DeadSession:
    """A session whose pipeline has gone away, as the bus watch reports it."""

    is_connected = False
    is_recording = True
    session_id = "session-1"
    end_reason = "source: Internal data stream error"

    def get_stats(self) -> dict:
        return {}


def test_startup_reconciles_before_claiming_worker_slot(worker_env) -> None:
    """_register_worker clears active_channels, so it must run second."""
    worker, factory = worker_env
    channel_id = _seed(factory, status="recording")

    worker._startup()

    status, recordings = _state(factory, channel_id)
    assert status == "idle"
    assert recordings == ["failed"]
    with factory() as db:
        row = db.query(Worker).filter_by(hostname="worker-1").one()
        assert channel_id not in (row.active_channels or [])


def test_heartbeat_finalizes_recording_when_session_dies(worker_env) -> None:
    """Nothing else can finalise it, and the API refuses to delete "recording"."""
    worker, factory = worker_env
    channel_id = _seed(factory, status="recording")
    worker._sessions = {channel_id: _DeadSession()}

    worker._heartbeat()

    assert _state(factory, channel_id) == ("error", ["failed"])
    assert channel_id not in worker._sessions


def test_heartbeat_survives_unavailable_redis(worker_env, monkeypatch) -> None:
    worker, factory = worker_env
    channel_id = _seed(factory, status="recording")
    worker._sessions = {channel_id: _DeadSession()}

    def _boom(_channel_id: str) -> None:
        raise RedisError("connection refused")

    monkeypatch.setattr("ingest_farm.worker.main.clear_channel_stats", _boom)
    worker._heartbeat()

    assert _state(factory, channel_id) == ("error", ["failed"])


def test_record_stop_without_session_finalizes_recording(worker_env) -> None:
    worker, factory = worker_env
    channel_id = _seed(factory, status="stopping")

    worker._handle_record_stop({"channel_id": channel_id})

    assert _state(factory, channel_id) == ("idle", ["failed"])


def test_stop_beats_start_queued_in_the_same_tick(worker_env) -> None:
    """Per-op Redis lists lose ordering; the stored intent breaks the tie."""
    worker, factory = worker_env
    channel_id = _seed(factory, status="stopping", recording=None)

    assert worker._resolve_ops(channel_id, ["stop", "start"]) == ["stop"]


def test_start_beats_stop_when_start_was_requested_last(worker_env) -> None:
    worker, factory = worker_env
    channel_id = _seed(factory, status="starting", recording=None)

    assert worker._resolve_ops(channel_id, ["stop", "start"]) == ["start"]


def test_disconnect_supersedes_record_ops(worker_env) -> None:
    worker, factory = worker_env
    channel_id = _seed(factory, status="disconnecting", recording=None)

    assert worker._resolve_ops(channel_id, ["disconnect", "stop", "start"]) == ["disconnect"]


def test_single_op_is_applied_unchanged(worker_env) -> None:
    worker, factory = worker_env
    channel_id = _seed(factory, status="connected", recording=None)

    assert worker._resolve_ops(channel_id, ["start"]) == ["start"]
