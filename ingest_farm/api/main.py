from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse, Response
from redis import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, contains_eager, joinedload

from ingest_farm.common.cleanup import delete_recording_media
from ingest_farm.common.events import get_channel_stats, get_channel_stats_many, get_redis
from ingest_farm.config import get_settings
from ingest_farm.db import get_db, get_engine
from ingest_farm.models import Asset, Channel, Recording, Worker
from ingest_farm.orchestrator.scheduler import JobPublishError, Scheduler
from ingest_farm.pipeline.encoders.registry import default_registry
from ingest_farm.schemas import (
    AssetResponse,
    AssetUpdate,
    ChannelCreate,
    ChannelResponse,
    ChannelUpdate,
    OutputConfig,
    PipelineConfig,
    RecordingResponse,
    RecordingStatus,
    RecordingUpdate,
    SourceConfig,
    WorkerResponse,
)

logger = logging.getLogger(__name__)

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
        # This catch-all is registered after the API routers, so without this
        # guard an unknown /api path would answer with index.html and a 200.
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
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


_LIVE_CHANNEL_STATUSES = {
    "connected",
    "recording",
    "connecting",
    "starting",
    "stopping",
    "disconnecting",
}


def _channel_urls(channel: Channel, stats: dict | None = None) -> dict[str, str | None]:
    live = channel.status in _LIVE_CHANNEL_STATUSES
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


def _channel_response(
    channel: Channel,
    stats_cache: dict[str, dict | None] | None = None,
) -> ChannelResponse:
    profile = channel.pipeline_profile or {}
    output = channel.output_config or {}
    stats = None
    if channel.status in _LIVE_CHANNEL_STATUSES:
        if stats_cache is not None:
            stats = stats_cache.get(channel.id)
        else:
            # Runtime stats are best-effort: a channel listing must still work
            # when Redis does not.
            try:
                stats = get_channel_stats(channel.id)
            except RedisError:
                logger.warning("Could not read stats for channel %s", channel.id, exc_info=True)
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


def _recording_response(recording: Recording) -> RecordingResponse:
    return RecordingResponse(
        id=recording.id,
        channel_id=recording.channel_id,
        started_at=recording.started_at,
        ended_at=recording.ended_at,
        status=RecordingStatus(recording.status),
        storage_path=recording.storage_path,
        segment_count=recording.segment_count,
        byte_size=recording.byte_size,
        metadata=recording.metadata_json or {},
    )


_IDLE_CHANNEL_STATUSES = {"idle", "error"}


def _require_idle_channel(channel: Channel) -> None:
    if channel.status not in _IDLE_CHANNEL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Channel must be idle or error (status={channel.status})",
        )


def _delete_recording(db: Session, recording: Recording) -> None:
    asset = db.query(Asset).filter_by(recording_id=recording.id).one_or_none()
    delete_recording_media(recording, asset)
    if asset is not None:
        db.delete(asset)
    db.delete(recording)


def _channel_action(
    db: Session,
    channel_id: str,
    action: Callable[[Session, str], None],
) -> ChannelResponse:
    """Request a lifecycle change and report the resulting intent.

    A 2xx here means the job was queued and the status is transitional, not
    that a pipeline is running.
    """
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    try:
        action(db, channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except JobPublishError as exc:
        raise HTTPException(
            status_code=503,
            detail="Job queue unavailable; the request was not accepted",
        ) from exc
    db.refresh(channel)
    return _channel_response(channel)


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
    # Exception text from these clients carries host, port and user, so it is
    # logged rather than returned to an unauthenticated caller.
    checks: dict[str, str] = {"api": "ok"}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except SQLAlchemyError:
        logger.warning("Health check: database unreachable", exc_info=True)
        checks["database"] = "error"
    try:
        checks["redis"] = "ok" if get_redis(socket_timeout=2).ping() else "error"
    except RedisError:
        logger.warning("Health check: redis unreachable", exc_info=True)
        checks["redis"] = "error"

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
    channels = list(db.query(Channel).order_by(Channel.created_at.desc()))
    live_ids = [c.id for c in channels if c.status in _LIVE_CHANNEL_STATUSES]
    stats_cache: dict[str, dict | None] = {}
    try:
        stats_cache = get_channel_stats_many(live_ids)
    except RedisError:
        logger.warning("Could not read channel stats for listing", exc_info=True)
    return [_channel_response(c, stats_cache) for c in channels]


@router.get("/channels/{channel_id}", response_model=ChannelResponse)
def get_channel(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    return _channel_response(channel)


@router.patch("/channels/{channel_id}", response_model=ChannelResponse)
def update_channel(
    channel_id: str,
    payload: ChannelUpdate,
    db: Session = Depends(get_db),
) -> ChannelResponse:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    _require_idle_channel(channel)

    if payload.name is not None:
        existing = (
            db.query(Channel)
            .filter(Channel.name == payload.name, Channel.id != channel_id)
            .one_or_none()
        )
        if existing:
            raise HTTPException(status_code=409, detail="Channel name already exists")
        channel.name = payload.name
    if payload.source is not None:
        channel.protocol = payload.source.protocol.value
        channel.source_uri = payload.source.uri
        channel.source_config = payload.source.config
    if payload.pipeline is not None:
        channel.pipeline_profile = payload.pipeline.model_dump(mode="json")
    if payload.output is not None:
        channel.output_config = payload.output.model_dump(mode="json")
    if payload.enabled is not None:
        channel.enabled = payload.enabled

    db.commit()
    db.refresh(channel)
    return _channel_response(channel)


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: str, db: Session = Depends(get_db)) -> Response:
    channel = db.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    _require_idle_channel(channel)

    recordings = db.query(Recording).filter_by(channel_id=channel_id).all()
    for recording in recordings:
        _delete_recording(db, recording)
    db.delete(channel)
    db.commit()
    return Response(status_code=204)


@router.post("/channels/{channel_id}/connect", response_model=ChannelResponse)
def connect_channel(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    return _channel_action(db, channel_id, scheduler.connect_channel)


@router.post("/channels/{channel_id}/disconnect", response_model=ChannelResponse)
def disconnect_channel(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    return _channel_action(db, channel_id, scheduler.disconnect_channel)


@router.post("/channels/{channel_id}/record/start", response_model=ChannelResponse)
def record_start(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    return _channel_action(db, channel_id, scheduler.start_recording)


@router.post("/channels/{channel_id}/record/stop", response_model=ChannelResponse)
def record_stop(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    return _channel_action(db, channel_id, scheduler.stop_recording)


@router.get("/channels/{channel_id}/thumbnail")
def channel_thumbnail(channel_id: str, db: Session = Depends(get_db)) -> FileResponse:
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
    except RedisError as exc:
        logger.warning("Stats read failed for channel %s", channel_id, exc_info=True)
        raise HTTPException(status_code=503, detail="Stats unavailable") from exc
    return {"channel_id": channel_id, "status": channel.status, "stats": stats}


@router.post("/channels/{channel_id}/start", response_model=ChannelResponse)
def start_channel(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    """Legacy: connect (if needed) and start recording."""
    return _channel_action(db, channel_id, scheduler.start_recording)


@router.post("/channels/{channel_id}/stop", response_model=ChannelResponse)
def stop_channel(channel_id: str, db: Session = Depends(get_db)) -> ChannelResponse:
    """Legacy: stop recording (stays connected if session is up)."""
    return _channel_action(db, channel_id, scheduler.stop_recording)


def _query_recordings(
    db: Session,
    *,
    channel_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[RecordingResponse]:
    query = db.query(Recording).order_by(Recording.started_at.desc())
    if channel_id:
        query = query.filter(Recording.channel_id == channel_id)
    if status:
        query = query.filter(Recording.status == status)
    return [_recording_response(r) for r in query.limit(limit).all()]


@router.get("/recordings", response_model=list[RecordingResponse])
def list_recordings(
    channel_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[RecordingResponse]:
    return _query_recordings(db, channel_id=channel_id, status=status, limit=limit)


@router.get("/recordings/{recording_id}", response_model=RecordingResponse)
def get_recording(recording_id: str, db: Session = Depends(get_db)) -> RecordingResponse:
    recording = db.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    return _recording_response(recording)


@router.patch("/recordings/{recording_id}", response_model=RecordingResponse)
def update_recording(
    recording_id: str,
    payload: RecordingUpdate,
    db: Session = Depends(get_db),
) -> RecordingResponse:
    recording = db.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    if recording.status == RecordingStatus.RECORDING.value:
        raise HTTPException(status_code=400, detail="Cannot update an active recording")
    if payload.metadata is not None:
        merged = dict(recording.metadata_json or {})
        merged.update(payload.metadata)
        recording.metadata_json = merged
    db.commit()
    db.refresh(recording)
    return _recording_response(recording)


@router.delete("/recordings/{recording_id}", status_code=204)
def delete_recording(recording_id: str, db: Session = Depends(get_db)) -> Response:
    recording = db.get(Recording, recording_id)
    if recording is None:
        raise HTTPException(status_code=404, detail="Recording not found")
    if recording.status == RecordingStatus.RECORDING.value:
        raise HTTPException(status_code=400, detail="Cannot delete an active recording")
    _delete_recording(db, recording)
    db.commit()
    return Response(status_code=204)


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


@router.patch("/assets/{asset_id}", response_model=AssetResponse)
def update_asset(
    asset_id: str,
    payload: AssetUpdate,
    db: Session = Depends(get_db),
) -> AssetResponse:
    asset = (
        db.query(Asset)
        .options(joinedload(Asset.recording).joinedload(Recording.channel))
        .filter(Asset.id == asset_id)
        .one_or_none()
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if payload.title is not None:
        asset.title = payload.title
    if payload.metadata is not None:
        merged = dict(asset.metadata_json or {})
        merged.update(payload.metadata)
        asset.metadata_json = merged
    db.commit()
    db.refresh(asset)
    return _asset_response(asset)


@router.delete("/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: str, db: Session = Depends(get_db)) -> Response:
    """Deletes the asset together with its recording and media on disk."""
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    recording = db.get(Recording, asset.recording_id)
    if recording is None:
        db.delete(asset)
        db.commit()
        return Response(status_code=204)
    if recording.status == RecordingStatus.RECORDING.value:
        raise HTTPException(status_code=400, detail="Cannot delete an active recording")
    _delete_recording(db, recording)
    db.commit()
    return Response(status_code=204)


@router.get("/assets/{asset_id}/master")
def download_master(asset_id: str, db: Session = Depends(get_db)) -> FileResponse:
    asset = db.get(Asset, asset_id)
    if asset is None or not asset.master_path:
        raise HTTPException(status_code=404, detail="Master not found")
    path = _safe_under(get_settings().storage_root, Path(asset.master_path))
    return FileResponse(path, media_type="video/mp2t", filename=path.name)


@router.get("/assets/{asset_id}/thumbnail")
def get_thumbnail(asset_id: str, db: Session = Depends(get_db)) -> FileResponse:
    asset = db.get(Asset, asset_id)
    if asset is None or not asset.thumbnail_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    path = _safe_under(get_settings().storage_root, Path(asset.thumbnail_path))
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
