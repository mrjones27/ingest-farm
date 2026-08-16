from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ingest_farm.config import get_settings
from ingest_farm.models import Asset, Recording

logger = logging.getLogger(__name__)


def _is_under(root: Path, candidate: Path) -> bool:
    root = root.resolve()
    target = candidate.resolve()
    return root == target or root in target.parents


def safe_remove_path(path: Path, storage_root: Path | None = None) -> bool:
    """Remove a file or directory only if it lies under storage_root."""
    root = (storage_root or get_settings().storage_root).resolve()
    target = path.resolve()
    if not _is_under(root, target):
        logger.warning("Refusing to delete path outside storage root: %s", target)
        return False
    if not target.exists():
        return False
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return True
    except OSError:
        logger.warning("Failed to delete %s", target, exc_info=True)
        return False


def _collect_extra_paths(recording: Recording, asset: Asset | None) -> list[Path]:
    """Paths that may lie outside recording.storage_path."""
    storage_dir = Path(recording.storage_path).resolve()
    extras: list[Path] = []
    if asset is None:
        return extras

    for raw in (asset.master_path, asset.proxy_path, asset.thumbnail_path):
        if not raw:
            continue
        path = Path(raw).resolve()
        if path == storage_dir or storage_dir in path.parents:
            continue
        extras.append(path)
        if path.is_file():
            continue
        if path.suffix == ".m3u8":
            extras.append(path.parent)
    return extras


def delete_recording_media(recording: Recording, asset: Asset | None = None) -> None:
    """Delete on-disk media for a recording and optional catalog asset."""
    storage_root = get_settings().storage_root
    storage_dir = Path(recording.storage_path)
    safe_remove_path(storage_dir, storage_root)

    for extra in _collect_extra_paths(recording, asset):
        safe_remove_path(extra, storage_root)
