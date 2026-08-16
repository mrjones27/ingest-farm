from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


class SourceProtocol(str, Enum):
    SRT = "srt"
    UDP = "udp"
    RTMP = "rtmp"
    HLS = "hls"
    FILE = "file"


class PipelineProfile(str, Enum):
    TS_PASSTHROUGH = "ts_passthrough"


_URI_SCHEMES: dict[SourceProtocol, set[str]] = {
    SourceProtocol.SRT: {"srt"},
    SourceProtocol.UDP: {"udp"},
    SourceProtocol.RTMP: {"rtmp", "rtmps"},
    SourceProtocol.HLS: {"http", "https"},
    SourceProtocol.FILE: {"file", ""},
}


def _validate_source_uri(protocol: SourceProtocol, uri: str) -> str:
    uri = uri.strip()
    if not uri:
        raise ValueError("uri must not be empty")
    # Reject GStreamer parse_launch injection characters.
    if '"' in uri or "!" in uri or "\n" in uri or "\r" in uri:
        raise ValueError('uri must not contain ", !, or newlines')
    if "://" in uri:
        scheme = uri.split("://", 1)[0].lower()
    else:
        scheme = ""
        if protocol == SourceProtocol.FILE:
            pass
        elif protocol == SourceProtocol.SRT and not uri.startswith("srt://"):
            uri = f"srt://{uri}"
            scheme = "srt"
        elif protocol == SourceProtocol.UDP and not uri.startswith("udp://"):
            uri = f"udp://{uri}"
            scheme = "udp"
    allowed = _URI_SCHEMES.get(protocol, set())
    if scheme not in allowed:
        raise ValueError(
            f"uri scheme '{scheme or '(none)'}' is not valid for protocol {protocol.value}"
        )
    return uri


class SourceConfig(BaseModel):
    protocol: SourceProtocol
    uri: str
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("uri")
    @classmethod
    def _uri_no_injection(cls, value: str) -> str:
        if not value or not str(value).strip():
            raise ValueError("uri must not be empty")
        raw = str(value).strip()
        if '"' in raw or "!" in raw or "\n" in raw or "\r" in raw:
            raise ValueError('uri must not contain ", !, or newlines')
        return raw

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "uri", _validate_source_uri(self.protocol, self.uri))


class PipelineConfig(BaseModel):
    profile: PipelineProfile = PipelineProfile.TS_PASSTHROUGH
    segment_duration_sec: int = 3600
    video: dict[str, Any] = Field(default_factory=dict)
    audio: dict[str, Any] = Field(default_factory=dict)

    @field_validator("profile", mode="before")
    @classmethod
    def _reject_unimplemented_profiles(cls, value: Any) -> Any:
        if value == "transcode_remux" or (
            isinstance(value, str) and value.lower() == "transcode_remux"
        ):
            raise ValueError(
                "pipeline profile 'transcode_remux' is not supported; use 'ts_passthrough'"
            )
        return value


class OutputConfig(BaseModel):
    container: str = "mpegts"
    path_template: str = "recordings/{channel_id}/{session_id}/segment_%05d.ts"


class ChannelConfig(BaseModel):
    name: str
    source: SourceConfig
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


class ChannelCreate(BaseModel):
    name: str
    source: SourceConfig
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)


class ChannelUpdate(BaseModel):
    name: str | None = None
    source: SourceConfig | None = None
    pipeline: PipelineConfig | None = None
    output: OutputConfig | None = None
    enabled: bool | None = None


class ChannelResponse(BaseModel):
    id: str
    name: str
    source: SourceConfig
    pipeline: PipelineConfig
    output: OutputConfig
    enabled: bool
    status: str = "idle"
    created_at: datetime
    urls: dict[str, str | None] = Field(default_factory=dict)
    stats: dict[str, Any] | None = None

    model_config = {"from_attributes": True}


class RecordingStatus(str, Enum):
    RECORDING = "recording"
    COMPLETED = "completed"
    FAILED = "failed"


class RecordingResponse(BaseModel):
    id: str
    channel_id: str
    started_at: datetime
    ended_at: datetime | None
    status: RecordingStatus
    storage_path: str
    segment_count: int
    byte_size: int
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class RecordingUpdate(BaseModel):
    metadata: dict[str, Any] | None = None


class AssetUpdate(BaseModel):
    title: str | None = None
    metadata: dict[str, Any] | None = None


class AssetResponse(BaseModel):
    id: str
    recording_id: str
    channel_id: str
    channel_name: str
    title: str
    duration_ms: int | None
    width: int | None
    height: int | None
    video_codec: str | None
    audio_codec: str | None
    master_path: str
    proxy_path: str | None
    thumbnail_path: str | None
    metadata: dict[str, Any]
    created_at: datetime
    urls: dict[str, str | None] = Field(default_factory=dict)

    model_config = {"from_attributes": True}


class WorkerResponse(BaseModel):
    id: str
    hostname: str
    last_heartbeat: datetime | None
    active_channels: list[str]
    capacity: int

    model_config = {"from_attributes": True}


def new_id() -> str:
    return str(uuid4())
