from __future__ import annotations

import logging
from pathlib import Path

from ingest_farm.common.events import POSTPROCESS_QUEUE, blocking_pop
from ingest_farm.common.gst_utils import discover_file, init_gstreamer
from ingest_farm.common.storage import LocalStorage
from ingest_farm.db import get_session_factory, init_db
from ingest_farm.models import Asset, Channel, Recording
from ingest_farm.postprocess.proxy import generate_hls_proxy
from ingest_farm.postprocess.thumbnail import generate_thumbnail

logger = logging.getLogger(__name__)


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
        self._backfill_missing_media()

        while self._running:
            job = blocking_pop(POSTPROCESS_QUEUE, timeout=5)
            if job:
                self._process(job)

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

    def _backfill_missing_media(self) -> None:
        """Fill proxy/thumbnail for cataloged assets that still have a master file."""
        with get_session_factory()() as db:
            assets = (
                db.query(Asset)
                .filter((Asset.proxy_path.is_(None)) | (Asset.thumbnail_path.is_(None)))
                .all()
            )
            logger.info("Backfill: %s asset(s) missing proxy and/or thumbnail", len(assets))
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

            master_path = segments[0]
            master_path_str = str(master_path)
            try:
                metadata.update(discover_file(master_path_str))
            except Exception:
                logger.warning("Could not discover metadata for %s", master_path_str)

            derived_dir = master_dir / "derived"
            proxy_path, thumbnail_path, media_meta = self._derive_media(master_path, derived_dir)
            metadata.update(media_meta)

            asset = Asset(
                recording_id=recording_id,
                title=(
                    f"{channel.name if channel else recording.channel_id} — "
                    f"{recording.started_at.isoformat()}"
                ),
                duration_ms=metadata.get("duration_ms"),
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
