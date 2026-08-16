from datetime import datetime, timezone
from pathlib import Path

from ingest_farm.models import Channel, Worker
from ingest_farm.schemas import new_id
from ingest_farm.worker.main import IngestWorker


def test_reconcile_only_clears_claimed_channels(tmp_path: Path, monkeypatch) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from ingest_farm.models import Base

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

    with factory() as db:
        mine = Channel(
            id=new_id(),
            name="mine",
            protocol="udp",
            source_uri="udp://0.0.0.0:5000",
            status="connected",
        )
        other = Channel(
            id=new_id(),
            name="other",
            protocol="udp",
            source_uri="udp://0.0.0.0:5001",
            status="connected",
        )
        db.add_all([mine, other])
        worker = Worker(
            hostname="worker-1",
            capacity=4,
            active_channels=[mine.id],
            last_heartbeat=datetime.now(timezone.utc),
        )
        db.add(worker)
        db.commit()
        mine_id, other_id = mine.id, other.id

    worker_obj = IngestWorker()
    worker_obj._sessions = {}
    worker_obj._reconcile_orphaned_channels()

    with factory() as db:
        assert db.get(Channel, mine_id).status == "idle"
        assert db.get(Channel, other_id).status == "connected"
        w = db.query(Worker).filter_by(hostname="worker-1").one()
        assert mine_id not in (w.active_channels or [])
