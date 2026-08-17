from __future__ import annotations

import logging
from pathlib import Path

from ingest_farm.common.events import POSTPROCESS_QUEUE, blocking_pop
from ingest_farm.common.gst_utils import discover_file, init_gstreamer
from ingest_farm.common.storage import LocalStorage
from ingest_farm.db import get_session_factory, init_db
from ingest_farm.models import Asset, Channel, Recording
from ingest_farm.postprocess.proxy import generate_hls_proxy, playlist_duration_ms
from ingest_farm.postprocess.thumbnail import generate_thumbnail
from ingest_farm.schemas import RecordingStatus

logger = logging.getLogger(__name__)

BACKFILL_BATCH = 5
# Discoverer on live MPEG-TS can return CLOCK_TIME_NONE or PCR-wrap junk.
_MAX_SANE_DURATION_MS = 12 * 3600 * 1000


def sane_media_ms(value: object) -> int | None:
    try:
        ms = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if ms <= 0 or ms > _MAX_SANE_DURATION_MS:
        return None
    return ms


def resolve_asset_duration_ms(
    *,
    playlist_ms: int | None,
    discover_ms: int | None,
    wall_ms: int | None,
) -> tuple[int | None, str]:
    """Prefer actual media time (HLS, then probe). Wall clock is the record window only."""
    if playlist_ms is not None and playlist_ms > 0:
        return playlist_ms, "hls_playlist"
    if discover_ms is not None and discover_ms > 0:
        if wall_ms is not None and discover_ms > wall_ms * 2:
            return wall_ms, "wall_clock"
        return discover_ms, "discoverer"
    if wall_ms is not None and wall_ms > 0:
        return wall_ms, "wall_clock"
    return None, "unknown"


def concat_mpegts_segments(segments: list[Path], master_path: Path) -> Path:
    """Concatenate MPEG-TS segments in order into a single master file."""
    if not segments:
        raise ValueError("No segments to concatenate")
    if len(segments) == 1:
        return segments[0]
    master_path.parent.mkdir(parents=True, exist_ok=True)
    with master_path.open("wb") as out:
        for seg in segments:
            with seg.open("rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
    return master_path


class PostProcessWorker:
    """Catalog completed recordings and generate HLS proxy + thumbnail."""

    def __init__(self) -> None:
        self.storage = LocalStorage()
        self._running = True

    def run(self) -> None:
        init_db()
        try:
            init_gstreamer()
        except Exception:
            logger.warning("GStreamer init failed — proxy/thumbnail may be unavailable")
        logger.info("Post-process worker started")
        self._catalog_uncataloged_recordings(limit=BACKFILL_BATCH)
        self._backfill_missing_media(limit=BACKFILL_BATCH)

        while self._running:
            job = blocking_pop(POSTPROCESS_QUEUE, timeout=5)
            if job:
                try:
                    self._process(job)
                except Exception:
                    logger.exception(
                        "Post-process job failed (recording_id=%s)",
                        job.get("recording_id"),
                    )

    def _derive_media(self, master_path: Path, derived_dir: Path) -> tuple[str | None, str | None, dict]:
        """Generate HLS proxy + thumbnail from a master media file."""
        metadata: dict = {}
        proxy_path: str | None = None
        thumbnail_path: str | None = None

        if not master_path.is_file():
            metadata["proxy"] = "skipped"
            metadata["thumbnail"] = "skipped"
            metadata["media_error"] = "master_not_a_file"
            return None, None, metadata

        try:
            playlist = generate_hls_proxy(master_path, derived_dir / "proxy")
            proxy_path = str(playlist)
            metadata["proxy"] = "ok"
        except Exception:
            logger.exception("HLS proxy generation failed for %s", master_path)
            metadata["proxy"] = "failed"

        try:
            thumb = generate_thumbnail(master_path, derived_dir / "thumb.jpg")
            thumbnail_path = str(thumb)
            metadata["thumbnail"] = "ok"
        except Exception:
            logger.exception("Thumbnail generation failed for %s", master_path)
            metadata["thumbnail"] = "failed"

        return proxy_path, thumbnail_path, metadata

    def _catalog_uncataloged_recordings(self, *, limit: int = BACKFILL_BATCH) -> None:
        """Catalog completed recordings that never produced an asset.

        Postprocess jobs are fire-and-forget, so a crash or restart mid-job
        otherwise leaves the recording uncataloged with nothing to retry it.
        """
        with get_session_factory()() as db:
            recording_ids = [
                row.id
                for row in db.query(Recording)
                .outerjoin(Asset, Asset.recording_id == Recording.id)
                .filter(
                    Recording.status == RecordingStatus.COMPLETED.value,
                    Asset.id.is_(None),
                )
                .order_by(Recording.started_at.desc())
                .limit(limit)
                .all()
            ]
        if not recording_ids:
            return
        logger.info("Startup catalog: %s completed recording(s) without an asset", len(recording_ids))
        for recording_id in recording_ids:
            try:
                self._process({"recording_id": recording_id})
            except Exception:
                logger.exception("Startup catalog failed for recording %s", recording_id)

    def _backfill_missing_media(self, *, limit: int = BACKFILL_BATCH) -> None:
        """Fill proxy/thumbnail for a bounded batch of cataloged assets."""
        with get_session_factory()() as db:
            pending = [
                (asset.id, asset.master_path)
                for asset in db.query(Asset)
                .filter((Asset.proxy_path.is_(None)) | (Asset.thumbnail_path.is_(None)))
                .order_by(Asset.created_at.desc())
                .limit(limit)
                .all()
            ]
        logger.info(
            "Backfill: processing up to %s asset(s) missing proxy and/or thumbnail (found %s)",
            limit,
            len(pending),
        )
        for asset_id, master_raw in pending:
            master = Path(master_raw)
            if not master.is_file():
                with get_session_factory()() as db:
                    asset = db.get(Asset, asset_id)
                    if asset is None:
                        continue
                    meta = dict(asset.metadata_json or {})
                    meta.setdefault("proxy", "skipped")
                    meta.setdefault("thumbnail", "skipped")
                    meta["media_error"] = "master_not_a_file"
                    asset.metadata_json = meta
                    db.commit()
                logger.info("Backfill skip asset %s — master is not a file", asset_id)
                continue

            # Transcoding happens with no session held; see _process.
            derived_dir = master.parent / "derived"
            proxy_path, thumbnail_path, media_meta = self._derive_media(master, derived_dir)

            with get_session_factory()() as db:
                asset = db.get(Asset, asset_id)
                if asset is None:
                    continue
                meta = dict(asset.metadata_json or {})
                meta.update(media_meta)
                if proxy_path:
                    asset.proxy_path = proxy_path
                if thumbnail_path:
                    asset.thumbnail_path = thumbnail_path
                asset.metadata_json = meta
                db.commit()
            logger.info(
                "Backfill asset %s (proxy=%s thumb=%s)",
                asset_id,
                bool(proxy_path),
                bool(thumbnail_path),
            )

    def _load_recording(self, recording_id: str) -> tuple[str, Path, int | None] | None:
        """Read the details the media work needs, then let the session go."""
        with get_session_factory()() as db:
            recording = db.get(Recording, recording_id)
            if recording is None:
                logger.error("Recording %s not found", recording_id)
                return None
            if db.query(Asset).filter_by(recording_id=recording_id).one_or_none():
                logger.info("Asset already exists for recording %s", recording_id)
                return None

            channel = db.get(Channel, recording.channel_id)
            title = (
                f"{channel.name if channel else recording.channel_id} — "
                f"{recording.started_at.isoformat()}"
            )
            wall_ms = None
            if recording.started_at and recording.ended_at:
                wall_ms = int((recording.ended_at - recording.started_at).total_seconds() * 1000)
            return title, Path(recording.storage_path), wall_ms

    def _create_asset(self, recording_id: str, **fields) -> None:
        with get_session_factory()() as db:
            if db.query(Asset).filter_by(recording_id=recording_id).one_or_none():
                logger.info("Asset for recording %s was created concurrently", recording_id)
                return
            asset = Asset(recording_id=recording_id, **fields)
            db.add(asset)
            db.commit()
            logger.info(
                "Created asset %s for recording %s (proxy=%s thumb=%s)",
                asset.id,
                recording_id,
                bool(fields.get("proxy_path")),
                bool(fields.get("thumbnail_path")),
            )

    def _process(self, job: dict) -> None:
        """Catalog one recording.

        Concat, discovery and transcoding run with no database session held:
        proxy generation alone is bounded by gst_proxy_timeout_sec, and holding
        a transaction open that long pins a Postgres connection.
        """
        recording_id = job["recording_id"]
        loaded = self._load_recording(recording_id)
        if loaded is None:
            return
        title, master_dir, wall_ms = loaded

        segments = self.storage.list_segments(master_dir)
        metadata: dict = {"segment_count": len(segments)}

        if not segments:
            logger.warning(
                "Recording %s has no .ts segments — cataloging without proxy/thumbnail",
                recording_id,
            )
            metadata["proxy"] = "skipped"
            metadata["thumbnail"] = "skipped"
            metadata["media_error"] = "no_segments"
            self._create_asset(
                recording_id,
                title=title,
                master_path=str(master_dir),
                proxy_path=None,
                thumbnail_path=None,
                metadata_json=metadata,
            )
            return

        if len(segments) == 1:
            master_path = segments[0]
        else:
            master_path = concat_mpegts_segments(segments, master_dir / "master.ts")
            metadata["concatenated_segments"] = len(segments)
        master_path_str = str(master_path)
        discover_ms = None
        try:
            discovered = discover_file(master_path_str)
            discover_ms = sane_media_ms(discovered.pop("duration_ms", None))
            metadata.update(discovered)
        except Exception:
            logger.warning("Could not discover metadata for %s", master_path_str)

        derived_dir = master_dir / "derived"
        proxy_path, thumbnail_path, media_meta = self._derive_media(master_path, derived_dir)
        metadata.update(media_meta)

        playlist_ms = None
        if proxy_path:
            playlist_ms = playlist_duration_ms(Path(proxy_path))
        duration_ms, duration_source = resolve_asset_duration_ms(
            playlist_ms=playlist_ms,
            discover_ms=discover_ms,
            wall_ms=wall_ms,
        )
        metadata["record_window_ms"] = wall_ms
        metadata["discover_duration_ms"] = discover_ms
        metadata["proxy_duration_ms"] = playlist_ms
        metadata["duration_ms"] = duration_ms
        metadata["duration_source"] = duration_source

        self._create_asset(
            recording_id,
            title=title,
            duration_ms=duration_ms,
            width=metadata.get("width"),
            height=metadata.get("height"),
            video_codec=metadata.get("video_codec"),
            audio_codec=metadata.get("audio_codec"),
            master_path=master_path_str,
            proxy_path=proxy_path,
            thumbnail_path=thumbnail_path,
            metadata_json=metadata,
        )
