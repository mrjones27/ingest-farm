from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from ingest_farm.common.events import get_redis
from ingest_farm.db import get_db, get_engine
from ingest_farm.models import Asset, Channel, Recording, Worker
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


def _asset_urls(asset: Asset) -> dict[str, str | None]:
    base = f"/assets/{asset.id}"
    return {
        "detail": base,
        "master": f"{base}/master" if asset.master_path else None,
        "thumbnail": f"{base}/thumbnail" if asset.thumbnail_path else None,
        "proxy_playlist": f"{base}/proxy/playlist.m3u8" if asset.proxy_path else None,
    }


def _asset_response(asset: Asset) -> AssetResponse:
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
        metadata=asset.metadata_json or {},
        created_at=asset.created_at,
        urls=_asset_urls(asset),
    )


def _safe_under(root: Path, candidate: Path) -> Path:
    root = root.resolve()
    target = candidate.resolve()
    if root not in target.parents and target != root:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return target


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
        if get_redis(socket_timeout=2).ping():
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
    try:
        scheduler.stop_channel(db, channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(channel)
    return _channel_response(channel)


@router.get("/assets", response_model=list[AssetResponse])
def list_assets(
    q: str | None = Query(default=None),
    channel_id: str | None = Query(default=None),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[AssetResponse]:
    query = db.query(Asset).order_by(Asset.created_at.desc())
    if q:
        query = query.filter(Asset.title.ilike(f"%{q}%"))
    if channel_id:
        query = query.join(Recording, Recording.id == Asset.recording_id).filter(
            Recording.channel_id == channel_id
        )
    if created_after:
        query = query.filter(Asset.created_at >= created_after)
    if created_before:
        query = query.filter(Asset.created_at <= created_before)
    return [_asset_response(a) for a in query.limit(limit).all()]


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
    return _asset_response(asset)


@router.get("/assets/{asset_id}/master")
def download_master(asset_id: str, db: Session = Depends(get_db)) -> FileResponse:
    asset = db.get(Asset, asset_id)
    if asset is None or not asset.master_path:
        raise HTTPException(status_code=404, detail="Master not found")
    path = Path(asset.master_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Master file missing on disk")
    return FileResponse(path, media_type="video/mp2t", filename=path.name)


@router.get("/assets/{asset_id}/thumbnail")
def get_thumbnail(asset_id: str, db: Session = Depends(get_db)) -> FileResponse:
    asset = db.get(Asset, asset_id)
    if asset is None or not asset.thumbnail_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    path = Path(asset.thumbnail_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail file missing on disk")
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


@router.get("/assets/{asset_id}/proxy/{file_path:path}")
def get_proxy_file(asset_id: str, file_path: str, db: Session = Depends(get_db)) -> Response:
    asset = db.get(Asset, asset_id)
    if asset is None or not asset.proxy_path:
        raise HTTPException(status_code=404, detail="Proxy not found")

    proxy_root = Path(asset.proxy_path).parent
    target = _safe_under(proxy_root, proxy_root / file_path)

    if target.suffix == ".m3u8":
        text_body = target.read_text(encoding="utf-8")
        rewritten = []
        for line in text_body.splitlines():
            if line and not line.startswith("#") and not line.startswith("http"):
                rewritten.append(f"/assets/{asset_id}/proxy/{line.strip()}")
            else:
                rewritten.append(line)
        return PlainTextResponse(
            "\n".join(rewritten) + "\n",
            media_type="application/vnd.apple.mpegurl",
        )

    media = "video/mp2t" if target.suffix == ".ts" else "application/octet-stream"
    return FileResponse(target, media_type=media, filename=target.name)


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
