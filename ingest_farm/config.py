from functools import lru_cache
from pathlib import Path
from typing import Literal

QueueKind = Literal["passthrough", "preview", "etr290"]

from pydantic_settings import BaseSettings, SettingsConfigDict

_LEAKY_MODES = {
    "none": 0,
    "no": 0,
    "off": 0,
    "upstream": 1,
    "downstream": 2,
}


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

    # --- SRT (URI query still wins when present) ---
    # Handshake / ARQ window. Must stay compatible with the caller (often 120–500).
    # This is NOT capture-buffer delay — do not raise it for recording stability.
    gst_srt_latency_ms: int = 500
    gst_srt_wait_for_connection: bool = True
    gst_srt_keep_listening: bool = True
    # srtsrc listener verdict for incoming callers. False rejects every caller.
    gst_srt_authentication: bool = True

    # --- UDP / RTP ---
    gst_udp_buffer_size: int = 8_388_608  # 8 MiB socket buffer
    gst_rtp_jitterbuffer_ms: int = 1000

    # --- Ingest queues (stability over latency: never drop on the record path) ---
    # leaky: none | upstream (drop when full) | downstream
    # Passthrough sits BEFORE the record tee. Byte+time caps so fullness is
    # real even when live TS timestamps are junk; leaky=none blocks instead of punching holes.
    gst_passthrough_queue_ms: int = 30000
    gst_passthrough_queue_bytes: int = 67_108_864  # 64 MiB
    gst_passthrough_queue_buffers: int = 0  # 0 = disabled; time/bytes decide
    gst_passthrough_leaky: str = "none"
    # Preview may drop. Buffer+byte limits are required or leaky never fires.
    gst_preview_queue_ms: int = 250
    gst_preview_queue_bytes: int = 1_048_576
    gst_preview_queue_buffers: int = 8
    gst_preview_leaky: str = "upstream"
    gst_tee_allow_not_linked: bool = True

    # --- ETR 290 (TSDuck tsp sidecar on SRT live sessions) ---
    etr290_enabled: bool = True
    etr290_interval_sec: int = 2
    # Monitor branch must leak so tsp cannot back-pressure ingest, but it needs
    # enough slack that leaky drops do not invent Priority 1 CC errors.
    etr290_queue_ms: int = 2000
    etr290_queue_bytes: int = 8_388_608  # 8 MiB
    etr290_queue_buffers: int = 0  # 0 = disabled; time/bytes decide
    etr290_queue_leaky: str = "upstream"

    # --- MPEG-TS capture ---
    gst_tsparse_set_timestamps: bool = True
    gst_tsparse_alignment: int = 7  # 7 = 188-byte packets

    # --- Live JPEG preview ---
    gst_thumb_width: int = 320
    gst_thumb_height: int = 180
    gst_thumb_jpeg_quality: int = 50
    gst_thumb_interval_sec: float = 2.0
    gst_thumb_idr_only: bool = True
    gst_parse_config_interval: int = -1  # -1 = SPS/PPS before every IDR

    # --- HLS proxy / catalog poster ---
    gst_proxy_width: int = 640
    gst_proxy_height: int = 360
    gst_proxy_bitrate_kbps: int = 800
    gst_proxy_key_int_max: int = 200
    gst_proxy_x264_tune: str = ""  # empty = no tune (do not use zerolatency for VOD)
    gst_proxy_x264_speed_preset: str = "veryfast"
    gst_proxy_hls_target_duration: int = 4
    gst_proxy_timeout_sec: int = 1800
    gst_poster_jpeg_quality: int = 85
    gst_poster_timeout_sec: int = 60
    gst_discoverer_timeout_sec: int = 5

    @property
    def resolved_worker_id(self) -> str:
        if self.worker_id:
            return self.worker_id
        import socket

        return socket.gethostname()

    def leaky(self, kind: QueueKind = "passthrough") -> int:
        raw = {
            "passthrough": self.gst_passthrough_leaky,
            "preview": self.gst_preview_leaky,
            "etr290": self.etr290_queue_leaky,
        }[kind]
        fallback = 0 if kind == "passthrough" else 1
        return _LEAKY_MODES.get(raw.strip().lower(), fallback)

    def queue_time_ns(self, kind: QueueKind = "passthrough") -> int:
        ms = {
            "passthrough": self.gst_passthrough_queue_ms,
            "preview": self.gst_preview_queue_ms,
            "etr290": self.etr290_queue_ms,
        }[kind]
        return max(0, int(ms)) * 1_000_000

    def configure_queue(self, queue, kind: QueueKind = "passthrough") -> None:
        """Apply leaky/size policy. Capture never leaks; preview/ETR290 can."""
        queue.set_property("leaky", self.leaky(kind))
        queue.set_property("max-size-time", self.queue_time_ns(kind))
        if kind == "passthrough":
            queue.set_property("max-size-bytes", int(self.gst_passthrough_queue_bytes))
            queue.set_property("max-size-buffers", int(self.gst_passthrough_queue_buffers))
        elif kind == "etr290":
            queue.set_property("max-size-bytes", int(self.etr290_queue_bytes))
            queue.set_property("max-size-buffers", int(self.etr290_queue_buffers))
        else:
            queue.set_property("max-size-bytes", int(self.gst_preview_queue_bytes))
            queue.set_property("max-size-buffers", int(self.gst_preview_queue_buffers))

    def ensure_srt_latency(self, uri: str) -> str:
        if "latency=" in uri:
            return uri
        sep = "&" if "?" in uri else "?"
        return f"{uri}{sep}latency={int(self.gst_srt_latency_ms)}"

    def configure_tsparse(self, tsparse) -> None:
        tsparse.set_property("set-timestamps", self.gst_tsparse_set_timestamps)
        if tsparse.find_property("alignment"):
            tsparse.set_property("alignment", int(self.gst_tsparse_alignment))

    def configure_tee(self, tee) -> None:
        if self.gst_tee_allow_not_linked and tee.find_property("allow-not-linked"):
            tee.set_property("allow-not-linked", True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
