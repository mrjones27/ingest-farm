from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://ingest:ingest@localhost:5432/ingest_farm"
    redis_url: str = "redis://localhost:6379/0"
    storage_root: Path = Path("./data/recordings")

    api_host: str = "0.0.0.0"
    api_port: int = 8080

    worker_id: str = ""
    worker_capacity: int = 4

    gst_plugin_path: str = ""

    @property
    def resolved_worker_id(self) -> str:
        if self.worker_id:
            return self.worker_id
        import socket

        return socket.gethostname()


@lru_cache
def get_settings() -> Settings:
    return Settings()
