export type SourceProtocol = "srt" | "udp" | "rtmp" | "hls" | "file";
export type PipelineProfile = "ts_passthrough";

export interface SourceConfig {
  protocol: SourceProtocol;
  uri: string;
  config: Record<string, unknown>;
}

export interface PipelineConfig {
  profile: PipelineProfile;
  segment_duration_sec: number;
  video: Record<string, unknown>;
  audio: Record<string, unknown>;
}

export interface OutputConfig {
  container: string;
  path_template: string;
}

export interface Channel {
  id: string;
  name: string;
  source: SourceConfig;
  pipeline: PipelineConfig;
  output: OutputConfig;
  enabled: boolean;
  status: string;
  created_at: string;
  urls: {
    thumbnail?: string | null;
  };
  stats?: Record<string, unknown> | null;
}

export interface ChannelCreate {
  name: string;
  source: SourceConfig;
  pipeline?: Partial<PipelineConfig>;
  output?: Partial<OutputConfig>;
}

export interface ChannelUpdate {
  name?: string;
  source?: SourceConfig;
  pipeline?: Partial<PipelineConfig>;
  output?: Partial<OutputConfig>;
  enabled?: boolean;
}

export type RecordingStatus = "recording" | "completed" | "failed";

export interface Recording {
  id: string;
  channel_id: string;
  started_at: string;
  ended_at: string | null;
  status: RecordingStatus;
  storage_path: string;
  segment_count: number;
  byte_size: number;
  metadata: Record<string, unknown>;
}

export interface RecordingUpdate {
  metadata?: Record<string, unknown>;
}

export interface AssetUpdate {
  title?: string;
  metadata?: Record<string, unknown>;
}

export interface Asset {
  id: string;
  recording_id: string;
  channel_id: string;
  channel_name: string;
  title: string;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  master_path: string;
  proxy_path: string | null;
  thumbnail_path: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  urls: {
    detail?: string | null;
    master?: string | null;
    thumbnail?: string | null;
    proxy_playlist?: string | null;
  };
}

export interface Worker {
  id: string;
  hostname: string;
  last_heartbeat: string | null;
  active_channels: string[];
  capacity: number;
}

export interface HealthStatus {
  status: string;
  checks: Record<string, string>;
}

export interface AssetListParams {
  q?: string;
  channel_id?: string;
  limit?: number;
}

export interface RecordingListParams {
  channel_id?: string;
  status?: RecordingStatus;
  limit?: number;
}
