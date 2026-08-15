from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ingest_farm.common.events import get_redis
from ingest_farm.db import get_db, get_engine
from ingest_farm.models import Asset, Channel, Worker
from ingest_farm.orchestrator.scheduler import Scheduler
from ingest_farm.pipeline.encoders.registry import default_registry
from ingest_farm.schemas import (
    AssetResponse,
    ChannelCreate,
    ChannelResponse,
    OutputConfig,
    PipelineConfig,
    SourceConfig,
    WorkerResponse,
)

router = APIRouter()
scheduler = Scheduler()


def create_app() -> FastAPI:
    app = FastAPI(title="Ingest Farm", version="0.1.0")
    app.include_router(router)
    return app


def _channel_response(channel: Channel) -> ChannelResponse:
    profile = channel.pipeline_profile or {}
    output = channel.output_config or {}
    return ChannelResponse(
        id=channel.id,
        name=channel.name,
        source=SourceConfig(
            protocol=channel.protocol,
            uri=channel.source_uri,
            config=channel.source_config or {},
        ),
        pipeline=PipelineConfig(**profile),
        output=OutputConfig(**output),
        enabled=channel.enabled,
        status=channel.status,
        created_at=channel.created_at,
    )


@router.get("/health")
def health() -> dict:
    checks: dict[str, str] = {"api": "ok"}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
    try:
        if get_redis().ping():
            checks["redis"] = "ok"
        else:
            checks["redis"] = "error: ping failed"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    status = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": status, "checks": checks}


@router.get("/encoders")
def list_encoders() -> dict:
    registry = default_registry()
    return {
        "video_audio_encoders": registry.list_encoders(),
        "framerate_converters": registry.list_framerate_converters(),
    }


@router.post("/channels", response_model=ChannelResponse)
def create_channel(payload: ChannelCreate, db: Session = Depends(get_db)) -> ChannelResponse:
    existing = db.query(Channel).filter_by(name=payload.name).one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Channel name already exists")

    channel = Channel(
        name=payload.name,
        protocol=payload.source.protocol.value,
        source_uri=payload.source.uri,
        source_config=payload.source.config,
        pipeline_profile=payload.pipeline.model_dump(mode="json"),
        output_config=payload.output.model_dump(mode="json"),
    )
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return _channel_response(channel)


@router.get("/channels", response_model=list[ChannelResponse])
def list_channels(db: Session = Depends(get_db)) -> list[ChannelResponse]:
    return [_channel_response(c) for c in db.query(Channel).order_by(Channel.created_at.desc())]


@router.get("/channels/{channel_id}", response_model=ChannelResponse)
def get_channel(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    return _channel_response(channel)


@router.post("/channels/{channel_id}/start", response_model=ChannelResponse)
def start_channel(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        scheduler.start_channel(db, channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(channel)
    return _channel_response(channel)


@router.post("/channels/{channel_id}/stop", response_model=ChannelResponse)
def stop_channel(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    scheduler.stop_channel(db, channel_id)
    db.refresh(channel)
    return _channel_response(channel)


@router.get("/assets", response_model=list[AssetResponse])
def list_assets(
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[AssetResponse]:
    query = db.query(Asset).order_by(Asset.created_at.desc())
    if q:
        query = query.filter(Asset.title.ilike(f"%{q}%"))
    assets = query.limit(100).all()
    return [
        AssetResponse(
            id=a.id,
            recording_id=a.recording_id,
            title=a.title,
            duration_ms=a.duration_ms,
            width=a.width,
            height=a.height,
            video_codec=a.video_codec,
            audio_codec=a.audio_codec,
            master_path=a.master_path,
            proxy_path=a.proxy_path,
            thumbnail_path=a.thumbnail_path,
            metadata=a.metadata_json,
            created_at=a.created_at,
        )
        for a in assets
    ]


@router.get("/assets/search", response_model=list[AssetResponse])
def search_assets(
    q: str = Query(...),
    db: Session = Depends(get_db),
) -> list[AssetResponse]:
    return list_assets(q=q, db=db)


@router.get("/assets/{asset_id}", response_model=AssetResponse)
def get_asset(asset_id: str, db: Session = Depends(get_db)) -> AssetResponse:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return AssetResponse(
        id=asset.id,
        recording_id=asset.recording_id,
        title=asset.title,
        duration_ms=asset.duration_ms,
        width=asset.width,
        height=asset.height,
        video_codec=asset.video_codec,
        audio_codec=asset.audio_codec,
        master_path=asset.master_path,
        proxy_path=asset.proxy_path,
        thumbnail_path=asset.thumbnail_path,
        metadata=asset.metadata_json,
        created_at=asset.created_at,
    )


@router.get("/workers", response_model=list[WorkerResponse])
def list_workers(db: Session = Depends(get_db)) -> list[WorkerResponse]:
    workers = db.query(Worker).all()
    return [
        WorkerResponse(
            id=w.id,
            hostname=w.hostname,
            last_heartbeat=w.last_heartbeat,
            active_channels=w.active_channels or [],
            capacity=w.capacity,
        )
        for w in workers
    ]
