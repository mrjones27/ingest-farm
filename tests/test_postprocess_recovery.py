"""Postprocess jobs are fire-and-forget, so startup must find what was lost."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ingest_farm.models import Asset, Base, Channel, Recording
from ingest_farm.postprocess.main import PostProcessWorker
from ingest_farm.schemas import new_id


@pytest.fixture
def postprocess_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    class _Settings:
        storage_root = tmp_path

    monkeypatch.setattr("ingest_farm.common.storage.get_settings", lambda: _Settings())
    monkeypatch.setattr("ingest_farm.postprocess.main.get_session_factory", lambda: factory)
    return PostProcessWorker(), factory, tmp_path


def _recording(factory, tmp_path: Path, *, status: str, with_asset: bool = False) -> str:
    with factory() as db:
        channel = Channel(
            id=new_id(),
            name=f"ch-{status}-{with_asset}",
            protocol="udp",
            source_uri="udp://0.0.0.0:5000",
        )
        db.add(channel)
        session_dir = tmp_path / channel.id / "session-1"
        session_dir.mkdir(parents=True)
        recording = Recording(
            id=new_id(),
            channel_id=channel.id,
            storage_path=str(session_dir),
            status=status,
        )
        db.add(recording)
        if with_asset:
            db.add(
                Asset(
                    id=new_id(),
                    recording_id=recording.id,
                    title="already cataloged",
                    master_path=str(session_dir),
                )
            )
        db.commit()
        return recording.id


def test_startup_catalogs_recording_whose_job_was_lost(postprocess_env) -> None:
    worker, factory, tmp_path = postprocess_env
    lost = _recording(factory, tmp_path, status="completed")
    cataloged = _recording(factory, tmp_path, status="completed", with_asset=True)
    still_running = _recording(factory, tmp_path, status="recording")

    worker._catalog_uncataloged_recordings()

    with factory() as db:
        recording_ids = {asset.recording_id for asset in db.query(Asset).all()}
        assert lost in recording_ids
        assert cataloged in recording_ids
        assert still_running not in recording_ids
        assert db.query(Asset).count() == 2
        asset = db.query(Asset).filter_by(recording_id=lost).one()
        assert asset.metadata_json["media_error"] == "no_segments"


def test_startup_catalog_is_a_no_op_when_nothing_is_missing(postprocess_env) -> None:
    worker, factory, tmp_path = postprocess_env
    _recording(factory, tmp_path, status="completed", with_asset=True)

    worker._catalog_uncataloged_recordings()

    with factory() as db:
        assert db.query(Asset).count() == 1
