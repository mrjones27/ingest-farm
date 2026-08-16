import type { Asset, AssetListParams, Channel, ChannelCreate, HealthStatus, Worker } from "./types";

export class ApiError extends Error {
  status: number;
  detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(url, { ...init, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "detail" in body) {
        const raw = (body as { detail: unknown }).detail;
        detail = typeof raw === "string" ? raw : JSON.stringify(raw);
      }
    } catch {
      /* ignore parse errors */
    }
    throw new ApiError(response.status, detail);
  }
  return (await response.json()) as T;
}

export function getHealth(): Promise<HealthStatus> {
  return request<HealthStatus>("/health");
}

export function listChannels(): Promise<Channel[]> {
  return request<Channel[]>("/api/channels");
}

export function getChannel(id: string): Promise<Channel> {
  return request<Channel>(`/api/channels/${id}`);
}

export function createChannel(payload: ChannelCreate): Promise<Channel> {
  return request<Channel>("/api/channels", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function connectChannel(id: string): Promise<Channel> {
  return request<Channel>(`/api/channels/${id}/connect`, { method: "POST" });
}

export function disconnectChannel(id: string): Promise<Channel> {
  return request<Channel>(`/api/channels/${id}/disconnect`, { method: "POST" });
}

export function startRecording(id: string): Promise<Channel> {
  return request<Channel>(`/api/channels/${id}/record/start`, { method: "POST" });
}

export function stopRecording(id: string): Promise<Channel> {
  return request<Channel>(`/api/channels/${id}/record/stop`, { method: "POST" });
}

/** @deprecated Prefer connect + startRecording */
export function startChannel(id: string): Promise<Channel> {
  return request<Channel>(`/api/channels/${id}/start`, { method: "POST" });
}

/** @deprecated Prefer stopRecording (keeps connection) or disconnectChannel */
export function stopChannel(id: string): Promise<Channel> {
  return request<Channel>(`/api/channels/${id}/stop`, { method: "POST" });
}

export function getChannelStats(id: string): Promise<{
  channel_id: string;
  status: string;
  stats: Record<string, unknown>;
}> {
  return request(`/api/channels/${id}/stats`);
}

export function listAssets(params: AssetListParams = {}): Promise<Asset[]> {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.channel_id) query.set("channel_id", params.channel_id);
  if (params.limit) query.set("limit", String(params.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request<Asset[]>(`/api/assets${suffix}`);
}

export function getAsset(id: string): Promise<Asset> {
  return request<Asset>(`/api/assets/${id}`);
}

export function listWorkers(): Promise<Worker[]> {
  return request<Worker[]>("/api/workers");
}
