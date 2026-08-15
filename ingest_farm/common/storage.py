from __future__ import annotations

from pathlib import Path

from ingest_farm.config import get_settings


class LocalStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().storage_root
        self.root.mkdir(parents=True, exist_ok=True)

    def session_dir(self, channel_id: str, session_id: str) -> Path:
        path = self.root / channel_id / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def list_segments(self, path: Path) -> list[Path]:
        return sorted(path.glob("*.ts"))

    def total_size(self, path: Path) -> int:
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
