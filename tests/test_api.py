from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from ingest_farm.api.main import create_app
from ingest_farm.db import get_db
from ingest_farm.models import Base


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
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
        yield test_client


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert "checks" in body
    assert body["checks"]["api"] == "ok"


def test_list_encoders(client: TestClient) -> None:
    response = client.get("/api/encoders")
    assert response.status_code == 200
    body = response.json()
    assert "gstreamer:x264enc" in body["video_audio_encoders"]
    assert "insync" in body["framerate_converters"]


def test_create_and_list_channel(client: TestClient) -> None:
    payload = {
        "name": "srt-test",
        "source": {"protocol": "srt", "uri": "srt://0.0.0.0:9000?mode=listener"},
        "pipeline": {"profile": "ts_passthrough", "segment_duration_sec": 3600},
        "output": {"container": "mpegts"},
    }
    created = client.post("/api/channels", json=payload)
    assert created.status_code == 200
    body = created.json()
    assert body["name"] == "srt-test"
    assert body["pipeline"]["profile"] == "ts_passthrough"
    assert body["status"] == "idle"
    assert body["urls"]["thumbnail"] is None

    listed = client.get("/api/channels")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_connect_and_record_endpoints(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from ingest_farm.api import main as api_main

    calls: list[str] = []

    def _track(name: str):
        def _fn(db, channel_id: str) -> None:  # noqa: ANN001
            calls.append(f"{name}:{channel_id}")
            channel = db.get(api_main.Channel, channel_id)
            status_map = {
                "connect": "connecting",
                "disconnect": "disconnecting",
                "record_start": "starting",
                "record_stop": "stopping",
            }
            channel.status = status_map[name]
            db.commit()

        return _fn

    monkeypatch.setattr(api_main.scheduler, "connect_channel", _track("connect"))
    monkeypatch.setattr(api_main.scheduler, "disconnect_channel", _track("disconnect"))
    monkeypatch.setattr(api_main.scheduler, "start_recording", _track("record_start"))
    monkeypatch.setattr(api_main.scheduler, "stop_recording", _track("record_stop"))

    created = client.post(
        "/api/channels",
        json={
            "name": "live-split",
            "source": {"protocol": "srt", "uri": "srt://0.0.0.0:9100?mode=listener"},
            "pipeline": {"profile": "ts_passthrough", "segment_duration_sec": 3600},
        },
    )
    assert created.status_code == 200
    channel_id = created.json()["id"]

    connected = client.post(f"/api/channels/{channel_id}/connect")
    assert connected.status_code == 200
    assert connected.json()["status"] == "connecting"
    assert connected.json()["urls"]["thumbnail"] == f"/api/channels/{channel_id}/thumbnail"

    recording = client.post(f"/api/channels/{channel_id}/record/start")
    assert recording.status_code == 200
    assert recording.json()["status"] == "starting"

    stopped = client.post(f"/api/channels/{channel_id}/record/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopping"

    disconnected = client.post(f"/api/channels/{channel_id}/disconnect")
    assert disconnected.status_code == 200
    assert disconnected.json()["status"] == "disconnecting"

    assert calls == [
        f"connect:{channel_id}",
        f"record_start:{channel_id}",
        f"record_stop:{channel_id}",
        f"disconnect:{channel_id}",
    ]


def test_channel_thumbnail_requires_live(
    client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ingest_farm.api import main as api_main
    from ingest_farm.config import get_settings
    from ingest_farm.models import Channel

    created = client.post(
        "/api/channels",
        json={
            "name": "thumb-ch",
            "source": {"protocol": "udp", "uri": "udp://0.0.0.0:5001"},
        },
    )
    channel_id = created.json()["id"]
    assert client.get(f"/api/channels/{channel_id}/thumbnail").status_code == 404

    monkeypatch.setattr(get_settings(), "storage_root", tmp_path)

    def _connect(db, channel_id: str) -> None:  # noqa: ANN001
        ch = db.get(Channel, channel_id)
        ch.status = "connected"
        db.commit()

    monkeypatch.setattr(api_main.scheduler, "connect_channel", _connect)
    client.post(f"/api/channels/{channel_id}/connect")

    thumb = tmp_path / channel_id / "live" / "thumb.jpg"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"\xff\xd8\xff" + b"\x00" * 200)

    resp = client.get(f"/api/channels/{channel_id}/thumbnail")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")


def test_spa_does_not_shadow_api(client: TestClient) -> None:
    from ingest_farm.api.main import WEB_DIST

    listed = client.get("/api/channels")
    assert listed.status_code == 200
    assert listed.headers["content-type"].startswith("application/json")

    docs = client.get("/docs")
    assert docs.status_code == 200

    if (WEB_DIST / "index.html").is_file():
        spa = client.get("/assets")
        assert spa.status_code == 200
        assert "text/html" in spa.headers["content-type"]
        root = client.get("/")
        assert root.status_code == 200
        assert "text/html" in root.headers["content-type"]
    else:
        assert client.get("/").status_code == 404
