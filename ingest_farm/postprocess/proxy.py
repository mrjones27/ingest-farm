"""HLS proxy generation from MPEG-TS masters (GStreamer hlssink2)."""

from __future__ import annotations

from pathlib import Path


def generate_hls_proxy(master_path: Path, output_dir: Path) -> Path:
    raise NotImplementedError(
        "HLS proxy generation will use decodebin → x264enc → hlssink2 in a later phase."
    )
