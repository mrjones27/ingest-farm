from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ingest_farm.api.main import create_app
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


def test_asset_media_serving(client_and_db, tmp_path: Path) -> None:
    client, factory = client_and_db

    master = tmp_path / "segment_00000.ts"
    master.write_bytes(b"\x47" + b"\x00" * 187)
    proxy_dir = tmp_path / "proxy"
    proxy_dir.mkdir()
    playlist = proxy_dir / "playlist.m3u8"
    playlist.write_text("#EXTM3U\n#EXTINF:4.0,\nsegment_00000.ts\n", encoding="utf-8")
    (proxy_dir / "segment_00000.ts").write_bytes(b"\x47" + b"\x00" * 187)
    thumb = tmp_path / "thumb.jpg"
    thumb.write_bytes(b"\xff\xd8\xff\xd9")

    with factory() as db:
        channel = Channel(
            id=new_id(),
            name="mam-channel",
            protocol="udp",
            source_uri="udp://0.0.0.0:5000",
        )
        db.add(channel)
        recording = Recording(
            id=new_id(),
            channel_id=channel.id,
            storage_path=str(tmp_path),
            status="completed",
        )
        db.add(recording)
        asset = Asset(
            id=new_id(),
            recording_id=recording.id,
            title="mam-channel — test",
            master_path=str(master),
            proxy_path=str(playlist),
            thumbnail_path=str(thumb),
            metadata_json={"segment_count": 1},
            created_at=datetime.now(timezone.utc),
        )
        db.add(asset)
        db.commit()
        asset_id = asset.id
        channel_id = channel.id

    listed = client.get(f"/api/assets?channel_id={channel_id}")
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["channel_id"] == channel_id
    assert body[0]["channel_name"] == "mam-channel"
    assert body[0]["urls"]["thumbnail"] == f"/api/assets/{asset_id}/thumbnail"
    assert body[0]["urls"]["proxy_playlist"] == f"/api/assets/{asset_id}/proxy/playlist.m3u8"

    detail = client.get(f"/api/assets/{asset_id}")
    assert detail.status_code == 200
    assert detail.json()["channel_name"] == "mam-channel"

    thumb_resp = client.get(f"/api/assets/{asset_id}/thumbnail")
    assert thumb_resp.status_code == 200
    assert thumb_resp.content.startswith(b"\xff\xd8")

    master_resp = client.get(f"/api/assets/{asset_id}/master")
    assert master_resp.status_code == 200
    assert master_resp.content[0:1] == b"\x47"

    playlist_resp = client.get(f"/api/assets/{asset_id}/proxy/playlist.m3u8")
    assert playlist_resp.status_code == 200
    assert f"/api/assets/{asset_id}/proxy/segment_00000.ts" in playlist_resp.text

    segment_resp = client.get(f"/api/assets/{asset_id}/proxy/segment_00000.ts")
    assert segment_resp.status_code == 200
    assert segment_resp.content[0:1] == b"\x47"
