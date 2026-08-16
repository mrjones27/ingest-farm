from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ingest_farm.api.main import create_app
from ingest_farm.config import get_settings
from ingest_farm.db import get_db
from ingest_farm.models import Asset, Base, Channel, Recording
from ingest_farm.schemas import new_id


@pytest.fixture
def client_and_db(tmp_path: Path) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db() -> Generator[Session, None, None]:
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client, factory


def _seed_recording(
    factory: sessionmaker,
    tmp_path: Path,
    *,
    status: str = "completed",
) -> tuple[str, str, Path]:
    session_dir = tmp_path / "channel-1" / "session-1"
    session_dir.mkdir(parents=True)
    segment = session_dir / "segment_00000.ts"
    segment.write_bytes(b"\x47" + b"\x00" * 187)

    with factory() as db:
        channel = Channel(
            id=new_id(),
            name="rec-channel",
            protocol="udp",
            source_uri="udp://0.0.0.0:5000",
        )
        db.add(channel)
        recording = Recording(
            id=new_id(),
            channel_id=channel.id,
            storage_path=str(session_dir),
            status=status,
            metadata_json={"note": "original"},
        )
        db.add(recording)
        asset = Asset(
            id=new_id(),
            recording_id=recording.id,
            title="rec-channel — test",
            master_path=str(segment),
            metadata_json={"segment_count": 1},
            created_at=datetime.now(timezone.utc),
        )
        db.add(asset)
        db.commit()
        return recording.id, asset.id, session_dir


def test_list_and_filter_recordings(client_and_db, tmp_path, monkeypatch) -> None:
    client, factory = client_and_db
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    recording_id, _, _ = _seed_recording(factory, tmp_path)

    listed = client.get("/api/recordings")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == recording_id

    filtered = client.get("/api/recordings?status=completed")
    assert filtered.status_code == 200
    assert len(filtered.json()) == 1

    empty = client.get("/api/recordings?status=recording")
    assert empty.status_code == 200
    assert empty.json() == []


def test_patch_recording_metadata(client_and_db, tmp_path, monkeypatch) -> None:
    client, factory = client_and_db
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    recording_id, _, _ = _seed_recording(factory, tmp_path)

    response = client.patch(
        f"/api/recordings/{recording_id}",
        json={"metadata": {"note": "updated", "tag": "ops"}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["note"] == "updated"
    assert body["metadata"]["tag"] == "ops"


def test_delete_recording_removes_files(client_and_db, tmp_path, monkeypatch) -> None:
    client, factory = client_and_db
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    recording_id, asset_id, session_dir = _seed_recording(factory, tmp_path)

    response = client.delete(f"/api/recordings/{recording_id}")
    assert response.status_code == 204
    assert not session_dir.exists()
    assert client.get(f"/api/recordings/{recording_id}").status_code == 404
    assert client.get(f"/api/assets/{asset_id}").status_code == 404


def test_delete_recording_blocked_while_active(client_and_db, tmp_path, monkeypatch) -> None:
    client, factory = client_and_db
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    recording_id, _, _ = _seed_recording(factory, tmp_path, status="recording")

    response = client.delete(f"/api/recordings/{recording_id}")
    assert response.status_code == 400


def test_patch_asset_title_and_metadata(client_and_db, tmp_path, monkeypatch) -> None:
    client, factory = client_and_db
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    _, asset_id, _ = _seed_recording(factory, tmp_path)

    response = client.patch(
        f"/api/assets/{asset_id}",
        json={"title": "Renamed asset", "metadata": {"reviewed": True}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Renamed asset"
    assert body["metadata"]["reviewed"] is True
    assert body["metadata"]["segment_count"] == 1


def test_delete_asset_hard_deletes(client_and_db, tmp_path, monkeypatch) -> None:
    client, factory = client_and_db
    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)
    recording_id, asset_id, session_dir = _seed_recording(factory, tmp_path)

    response = client.delete(f"/api/assets/{asset_id}")
    assert response.status_code == 204
    assert not session_dir.exists()
    assert client.get(f"/api/assets/{asset_id}").status_code == 404
    assert client.get(f"/api/recordings/{recording_id}").status_code == 404
