"""Poster-frame thumbnail extraction from MPEG-TS masters."""

from __future__ import annotations

import logging
from pathlib import Path

from ingest_farm.common.gst_utils import init_gstreamer, run_pipeline_string

logger = logging.getLogger(__name__)


def generate_thumbnail(master_path: Path, output_path: Path) -> Path:
    """Extract a single JPEG poster frame from the master."""
    init_gstreamer()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    desc = (
        f'filesrc location="{master_path}" ! decodebin ! '
        f"videoconvert ! videoscale ! video/x-raw,width=640,height=360 ! "
        f'jpegenc snapshot=true quality=85 ! filesink location="{output_path}"'
    )
    logger.info("Generating thumbnail for %s → %s", master_path, output_path)
    try:
        run_pipeline_string(desc, timeout_sec=60)
    except Exception:
        # Older jpegenc builds may lack snapshot=; fall back to first-frame grab.
        logger.warning("jpegenc snapshot failed; retrying without snapshot")
        desc = (
            f'filesrc location="{master_path}" ! decodebin ! '
            f"videoconvert ! videoscale ! video/x-raw,width=640,height=360 ! "
            f'jpegenc quality=85 ! multifilesink location="{output_path}" max-files=1'
        )
        run_pipeline_string(desc, timeout_sec=30)

    if not output_path.exists() or output_path.stat().st_size < 100:
        raise RuntimeError(f"Thumbnail was not created: {output_path}")
    return output_path
