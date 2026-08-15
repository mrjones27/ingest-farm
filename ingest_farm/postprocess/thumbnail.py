"""Poster-frame thumbnail extraction from MPEG-TS masters."""

from __future__ import annotations

from pathlib import Path


def generate_thumbnail(master_path: Path, output_path: Path) -> Path:
    raise NotImplementedError(
        "Thumbnail extraction will use videoconvert → jpegenc in a later phase."
    )
