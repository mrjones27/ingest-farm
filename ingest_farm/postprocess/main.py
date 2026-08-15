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

        while self._running:
            job = blocking_pop(POSTPROCESS_QUEUE, timeout=5)
            if job:
                self._process(job)

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
            master_path = segments[0] if segments else master_dir
            master_path_str = str(master_path)

            metadata: dict = {"segment_count": len(segments)}
            try:
                metadata.update(discover_file(master_path_str))
            except Exception:
                logger.warning("Could not discover metadata for %s", master_path_str)

            proxy_path: str | None = None
            thumbnail_path: str | None = None
            derived_dir = master_dir / "derived"

            try:
                playlist = generate_hls_proxy(Path(master_path_str), derived_dir / "proxy")
                proxy_path = str(playlist)
                metadata["proxy"] = "ok"
            except Exception:
                logger.exception("HLS proxy generation failed for %s", master_path_str)
                metadata["proxy"] = "failed"

            try:
                thumb = generate_thumbnail(Path(master_path_str), derived_dir / "thumb.jpg")
                thumbnail_path = str(thumb)
                metadata["thumbnail"] = "ok"
            except Exception:
                logger.exception("Thumbnail generation failed for %s", master_path_str)
                metadata["thumbnail"] = "failed"

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
