"""Poster-frame thumbnail extraction from MPEG-TS masters."""

from __future__ import annotations

import logging
from pathlib import Path

from ingest_farm.common.gst_utils import init_gstreamer, run_pipeline_string
from ingest_farm.config import get_settings

logger = logging.getLogger(__name__)


def generate_thumbnail(master_path: Path, output_path: Path) -> Path:
    """Extract a single JPEG poster frame from the master."""
    init_gstreamer()
    settings = get_settings()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    caps = (
        f"video/x-raw,width={int(settings.gst_proxy_width)},"
        f"height={int(settings.gst_proxy_height)}"
    )
    quality = int(settings.gst_poster_jpeg_quality)
    timeout = float(settings.gst_poster_timeout_sec)

    desc = (
        f'filesrc location="{master_path}" ! decodebin ! '
        f"videoconvert ! videoscale ! {caps} ! "
        f'jpegenc snapshot=true quality={quality} ! filesink location="{output_path}"'
    )
    logger.info("Generating thumbnail for %s → %s", master_path, output_path)
    try:
        run_pipeline_string(desc, timeout_sec=timeout)
    except Exception:
        logger.warning("jpegenc snapshot failed; retrying without snapshot")
        desc = (
            f'filesrc location="{master_path}" ! decodebin ! '
            f"videoconvert ! videoscale ! {caps} ! "
            f'jpegenc quality={quality} ! multifilesink location="{output_path}" max-files=1'
        )
        run_pipeline_string(desc, timeout_sec=max(15.0, timeout / 2))

    if not output_path.exists() or output_path.stat().st_size < 100:
        raise RuntimeError(f"Thumbnail was not created: {output_path}")
    return output_path
