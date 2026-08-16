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

    def _backfill_missing_media(self, *, limit: int = BACKFILL_BATCH) -> None:
        """Fill proxy/thumbnail for a bounded batch of cataloged assets."""
        with get_session_factory()() as db:
            assets = (
                db.query(Asset)
                .filter((Asset.proxy_path.is_(None)) | (Asset.thumbnail_path.is_(None)))
                .order_by(Asset.created_at.desc())
                .limit(limit)
                .all()
            )
            logger.info(
                "Backfill: processing up to %s asset(s) missing proxy and/or thumbnail (found %s)",
                limit,
                len(assets),
            )
            for asset in assets:
                master = Path(asset.master_path)
                if not master.is_file():
                    meta = dict(asset.metadata_json or {})
                    meta.setdefault("proxy", "skipped")
                    meta.setdefault("thumbnail", "skipped")
                    meta["media_error"] = "master_not_a_file"
                    asset.metadata_json = meta
                    logger.info("Backfill skip asset %s — master is not a file", asset.id)
                    continue

                derived_dir = master.parent / "derived"
                proxy_path, thumbnail_path, media_meta = self._derive_media(master, derived_dir)
                meta = dict(asset.metadata_json or {})
                meta.update(media_meta)
                if proxy_path:
                    asset.proxy_path = proxy_path
                if thumbnail_path:
                    asset.thumbnail_path = thumbnail_path
                asset.metadata_json = meta
                logger.info(
                    "Backfill asset %s (proxy=%s thumb=%s)",
                    asset.id,
                    bool(proxy_path),
                    bool(thumbnail_path),
                )
            db.commit()

    def _process(self, job: dict) -> None:
        recording_id = job["recording_id"]
        with get_session_factory()() as db:
            recording = db.get(Recording, recording_id)
            if recording is None:
                logger.error("Recording %s not found", recording_id)
                return
            existing = db.query(Asset).filter_by(recording_id=recording_id).one_or_none()
            if existing:
                logger.info("Asset already exists for recording %s", recording_id)
                return

            channel = db.get(Channel, recording.channel_id)
            master_dir = Path(recording.storage_path)
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
                asset = Asset(
                    recording_id=recording_id,
                    title=(
                        f"{channel.name if channel else recording.channel_id} — "
                        f"{recording.started_at.isoformat()}"
                    ),
                    master_path=str(master_dir),
                    proxy_path=None,
                    thumbnail_path=None,
                    metadata_json=metadata,
                )
                db.add(asset)
                db.commit()
                return

            if len(segments) == 1:
                master_path = segments[0]
            else:
                master_path = concat_mpegts_segments(segments, master_dir / "master.ts")
                metadata["concatenated_segments"] = len(segments)
            master_path_str = str(master_path)
            wall_ms = None
            if recording.started_at and recording.ended_at:
                wall_ms = int(
                    (recording.ended_at - recording.started_at).total_seconds() * 1000
                )
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

            asset = Asset(
                recording_id=recording_id,
                title=(
                    f"{channel.name if channel else recording.channel_id} — "
                    f"{recording.started_at.isoformat()}"
                ),
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
            db.add(asset)
            db.commit()
            logger.info(
                "Created asset %s for recording %s (proxy=%s thumb=%s)",
                asset.id,
                recording_id,
                bool(proxy_path),
                bool(thumbnail_path),
            )
