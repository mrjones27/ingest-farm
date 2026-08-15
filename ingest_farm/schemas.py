from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class SourceProtocol(str, Enum):
    SRT = "srt"
    UDP = "udp"
    RTMP = "rtmp"
    HLS = "hls"
    FILE = "file"


class PipelineProfile(str, Enum):
    TS_PASSTHROUGH = "ts_passthrough"
    TRANSCODE_REMUX = "transcode_remux"


class SourceConfig(BaseModel):
    protocol: SourceProtocol
    uri: str
    config: dict[str, Any] = Field(default_factory=dict)


class PipelineConfig(BaseModel):
    profile: PipelineProfile = PipelineProfile.TS_PASSTHROUGH
    segment_duration_sec: int = 3600
    video: dict[str, Any] = Field(default_factory=dict)
    audio: dict[str, Any] = Field(default_factory=dict)


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


class ChannelResponse(BaseModel):
    id: str
    name: str
    source: SourceConfig
    pipeline: PipelineConfig
    output: OutputConfig
    enabled: bool
    status: str = "idle"
    created_at: datetime

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

    model_config = {"from_attributes": True}


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
