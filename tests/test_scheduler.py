"""Scheduler must not report a state change it failed to queue."""
from __future__ import annotations

import pytest
from redis import RedisError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ingest_farm.models import Base, Channel
from ingest_farm.orchestrator.scheduler import JobPublishError, Scheduler
from ingest_farm.schemas import new_id


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as session:
        yield session


def _channel(db, status: str) -> str:
    channel = Channel(
        id=new_id(),
        name="sched-ch",
        protocol="udp",
        source_uri="udp://0.0.0.0:5000",
        status=status,
    )
    db.add(channel)
    db.commit()
    return channel.id


def test_intent_is_committed_before_the_job_is_published(db_session, monkeypatch) -> None:
    """A fast worker must not have its achieved state overwritten by the intent."""
    observed: list[str] = []

    def _publish(_queue: str, _payload: dict) -> None:
        observed.append(db_session.get(Channel, channel_id).status)

    channel_id = _channel(db_session, "idle")
    monkeypatch.setattr("ingest_farm.orchestrator.scheduler.publish_event", _publish)

    Scheduler().connect_channel(db_session, channel_id)

    assert observed == ["connecting"]


def test_publish_failure_reverts_status_and_raises(db_session, monkeypatch) -> None:
    def _publish(_queue: str, _payload: dict) -> None:
        raise RedisError("connection refused")

    channel_id = _channel(db_session, "idle")
    monkeypatch.setattr("ingest_farm.orchestrator.scheduler.publish_event", _publish)

    with pytest.raises(JobPublishError):
        Scheduler().connect_channel(db_session, channel_id)

    assert db_session.get(Channel, channel_id).status == "idle"


def test_stop_publish_failure_leaves_channel_recording(db_session, monkeypatch) -> None:
    def _publish(_queue: str, _payload: dict) -> None:
        raise RedisError("connection refused")

    channel_id = _channel(db_session, "recording")
    monkeypatch.setattr("ingest_farm.orchestrator.scheduler.publish_event", _publish)

    with pytest.raises(JobPublishError):
        Scheduler().stop_recording(db_session, channel_id)

    assert db_session.get(Channel, channel_id).status == "recording"
