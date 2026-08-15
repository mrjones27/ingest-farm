"""HLS proxy generation from MPEG-TS masters (GStreamer hlssink2)."""

from __future__ import annotations

import logging
from pathlib import Path

from ingest_farm.common.gst_utils import init_gstreamer, run_pipeline_string

logger = logging.getLogger(__name__)


def generate_hls_proxy(master_path: Path, output_dir: Path) -> Path:
    """Transcode master media to a low-res HLS package for web preview."""
    init_gstreamer()
    output_dir.mkdir(parents=True, exist_ok=True)
    playlist = output_dir / "playlist.m3u8"
    segment_pattern = output_dir / "segment_%05d.ts"

    # Video-only proxy keeps linking simple across GStreamer versions.
    desc = (
        f'filesrc location="{master_path}" ! decodebin ! '
        f"videoconvert ! videoscale ! video/x-raw,width=640,height=360 ! "
        f"x264enc tune=zerolatency bitrate=800 key-int-max=50 ! h264parse ! "
        f'hlssink2 location="{segment_pattern}" playlist-location="{playlist}" '
        f"target-duration=4 max-files=0"
    )
    logger.info("Generating HLS proxy for %s → %s", master_path, playlist)
    run_pipeline_string(desc, timeout_sec=300)
    if not playlist.exists():
        raise RuntimeError(f"HLS playlist was not created: {playlist}")
    return playlist
