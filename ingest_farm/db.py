from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ingest_farm.config import get_settings

logger = logging.getLogger(__name__)

_engine = None
SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global SessionLocal
    if SessionLocal is None:
        SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return SessionLocal


def _alembic_config():
    from alembic.config import Config

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url)
    return cfg


def init_db() -> None:
    """Apply Alembic migrations to head (source of truth; no create_all).

    Databases created by the old ``Base.metadata.create_all`` path have tables
    but no ``alembic_version`` row. Stamp those at the initial revision so
    upgrade does not try to recreate existing tables.
    """
    from alembic import command
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import inspect

    cfg = _alembic_config()
    engine = get_engine()
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()

    if current is None and "channels" in tables:
        logger.info("Existing schema has no Alembic revision; stamping 0001_initial")
        command.stamp(cfg, "0001_initial")

    logger.info("Running alembic upgrade head")
    command.upgrade(cfg, "head")


def get_db():
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
