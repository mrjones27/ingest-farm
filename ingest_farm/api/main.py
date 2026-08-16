from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy import text
from sqlalchemy.orm import Session, contains_eager, joinedload

from ingest_farm.common.events import get_channel_stats, get_redis
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

health_router = APIRouter()
router = APIRouter()
scheduler = Scheduler()

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="Ingest Farm", version="0.1.0")
    app.include_router(health_router)
    app.include_router(router, prefix="/api")
    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    index = WEB_DIST / "index.html"
    if not index.is_file():
        return

    def _spa_file(full_path: str) -> FileResponse:
        dist_root = WEB_DIST.resolve()
        if full_path:
            candidate = (WEB_DIST / full_path).resolve()
            if dist_root in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index)

    @app.get("/")
    def spa_root() -> FileResponse:
        return FileResponse(index)

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        return _spa_file(full_path)


def _channel_urls(channel: Channel, stats: dict | None = None) -> dict[str, str | None]:
    live = channel.status in {
        "connected",
        "recording",
        "connecting",
        "starting",
        "stopping",
        "disconnecting",
    }
    # Don't advertise thumbnail when SRT child fell back to fakesink preview.
    preview = (stats or {}).get("preview")
    if channel.protocol == "srt" and preview == "fakesink":
        return {"thumbnail": None}
    return {
        "thumbnail": f"/api/channels/{channel.id}/thumbnail" if live else None,
    }


def _pipeline_from_db(profile: dict | None) -> PipelineConfig:
    data = dict(profile or {})
    if data.get("profile") == "transcode_remux":
        data["profile"] = "ts_passthrough"
    return PipelineConfig(**data)


def _channel_response(channel: Channel) -> ChannelResponse:
    profile = channel.pipeline_profile or {}
    output = channel.output_config or {}
    stats = None
    if channel.status in {
        "connected",
        "recording",
        "connecting",
        "starting",
        "stopping",
        "disconnecting",
    }:
        try:
            stats = get_channel_stats(channel.id)
        except Exception:
            stats = None
    return ChannelResponse(
        id=channel.id,
        name=channel.name,
        source=SourceConfig(
            protocol=channel.protocol,
            uri=channel.source_uri,
            config=channel.source_config or {},
        ),
        pipeline=_pipeline_from_db(profile),
        output=OutputConfig(**output),
        enabled=channel.enabled,
        status=channel.status,
        created_at=channel.created_at,
        urls=_channel_urls(channel, stats),
        stats=stats,
    )


def _asset_urls(asset: Asset) -> dict[str, str | None]:
    base = f"/api/assets/{asset.id}"
    return {
        "detail": base,
        "master": f"{base}/master" if asset.master_path else None,
        "thumbnail": f"{base}/thumbnail" if asset.thumbnail_path else None,
        "proxy_playlist": f"{base}/proxy/playlist.m3u8" if asset.proxy_path else None,
    }


def _asset_response(asset: Asset) -> AssetResponse:
    recording = asset.recording
    channel = recording.channel if recording is not None else None
    return AssetResponse(
        id=asset.id,
        recording_id=asset.recording_id,
        channel_id=recording.channel_id if recording is not None else "",
        channel_name=channel.name if channel is not None else "",
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


@health_router.get("/health")
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


@router.post("/channels/{channel_id}/connect", response_model=ChannelResponse)
def connect_channel(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        scheduler.connect_channel(db, channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(channel)
    return _channel_response(channel)


@router.post("/channels/{channel_id}/disconnect", response_model=ChannelResponse)
def disconnect_channel(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        scheduler.disconnect_channel(db, channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(channel)
    return _channel_response(channel)


@router.post("/channels/{channel_id}/record/start", response_model=ChannelResponse)
def record_start(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        scheduler.start_recording(db, channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(channel)
    return _channel_response(channel)


@router.post("/channels/{channel_id}/record/stop", response_model=ChannelResponse)
def record_stop(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        scheduler.stop_recording(db, channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(channel)
    return _channel_response(channel)


@router.get("/channels/{channel_id}/thumbnail")
def channel_thumbnail(channel_id: str, db: Session = Depends(get_db)) -> FileResponse:
    from ingest_farm.config import get_settings

    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    if channel.status not in {
        "connected",
        "recording",
        "connecting",
        "starting",
        "stopping",
    }:
        raise HTTPException(status_code=404, detail="Channel not live")
    path = get_settings().storage_root / channel_id / "live" / "thumb.jpg"
    stable = path.with_name("thumb.ok.jpg")
    serve = path if path.is_file() and path.stat().st_size >= 100 else None
    if serve is None and stable.is_file() and stable.stat().st_size >= 100:
        serve = stable
    if serve is None:
        raise HTTPException(status_code=404, detail="Thumbnail not ready")
    return FileResponse(
        serve,
        media_type="image/jpeg",
        filename="thumb.jpg",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/channels/{channel_id}/stats")
def channel_stats(channel_id: str, db: Session = Depends(get_db)) -> dict:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        stats = get_channel_stats(channel_id) or {}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Stats unavailable: {exc}") from exc
    return {"channel_id": channel_id, "status": channel.status, "stats": stats}


@router.post("/channels/{channel_id}/start", response_model=ChannelResponse)
def start_channel(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    """Legacy: connect (if needed) and start recording."""
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        scheduler.start_recording(db, channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(channel)
    return _channel_response(channel)


@router.post("/channels/{channel_id}/stop", response_model=ChannelResponse)
def stop_channel(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    """Legacy: stop recording (stays connected if session is up)."""
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        scheduler.stop_recording(db, channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(channel)
    return _channel_response(channel)


def _query_assets(
    db: Session,
    *,
    q: str | None = None,
    channel_id: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = 100,
) -> list[AssetResponse]:
    query = (
        db.query(Asset)
        .join(Asset.recording)
        .join(Recording.channel)
        .options(contains_eager(Asset.recording).contains_eager(Recording.channel))
        .order_by(Asset.created_at.desc())
    )
    if q:
        query = query.filter(Asset.title.ilike(f"%{q}%"))
    if channel_id:
        query = query.filter(Recording.channel_id == channel_id)
    if created_after:
        query = query.filter(Asset.created_at >= created_after)
    if created_before:
        query = query.filter(Asset.created_at <= created_before)
    return [_asset_response(a) for a in query.limit(limit).all()]


@router.get("/assets", response_model=list[AssetResponse])
def list_assets(
    q: str | None = Query(default=None),
    channel_id: str | None = Query(default=None),
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[AssetResponse]:
    return _query_assets(
        db,
        q=q,
        channel_id=channel_id,
        created_after=created_after,
        created_before=created_before,
        limit=limit,
    )


@router.get("/assets/search", response_model=list[AssetResponse])
def search_assets(
    q: str = Query(...),
    db: Session = Depends(get_db),
) -> list[AssetResponse]:
    return _query_assets(db, q=q)


@router.get("/assets/{asset_id}", response_model=AssetResponse)
def get_asset(asset_id: str, db: Session = Depends(get_db)) -> AssetResponse:
    asset = (
        db.query(Asset)
        .options(joinedload(Asset.recording).joinedload(Recording.channel))
        .filter(Asset.id == asset_id)
        .one_or_none()
    )
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
        from ingest_farm.postprocess.proxy import rewrite_proxy_playlist

        text_body = target.read_text(encoding="utf-8")
        return PlainTextResponse(
            rewrite_proxy_playlist(text_body, asset_id),
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
