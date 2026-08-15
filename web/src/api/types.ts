export type SourceProtocol = "srt" | "udp" | "rtmp" | "hls" | "file";
export type PipelineProfile = "ts_passthrough" | "transcode_remux";

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
}

export interface ChannelCreate {
  name: string;
  source: SourceConfig;
  pipeline?: Partial<PipelineConfig>;
  output?: Partial<OutputConfig>;
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
