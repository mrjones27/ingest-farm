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
    response = client.get("/encoders")
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
    created = client.post("/channels", json=payload)
    assert created.status_code == 200
    body = created.json()
    assert body["name"] == "srt-test"
    assert body["pipeline"]["profile"] == "ts_passthrough"
    assert body["status"] == "idle"

    listed = client.get("/channels")
    assert listed.status_code == 200
    assert len(listed.json()) == 1
