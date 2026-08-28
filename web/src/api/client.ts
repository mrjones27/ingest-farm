import type {
  Asset,
  AssetListParams,
  AssetUpdate,
  Channel,
  ChannelCreate,
  ChannelUpdate,
  HealthStatus,
  Recording,
  RecordingListParams,
  RecordingUpdate,
  Worker,
} from "./types";

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

/** FastAPI sends 422 detail as a list of validation errors, not a string. */
function formatDetail(raw: unknown): string | null {
  if (typeof raw === "string") return raw;
  if (Array.isArray(raw)) {
    const parts = raw
      .map((item) => {
        if (!item || typeof item !== "object") return null;
        const { loc, msg } = item as { loc?: unknown; msg?: unknown };
        if (typeof msg !== "string") return null;
        const field = Array.isArray(loc)
          ? loc.filter((part) => part !== "body").join(".")
          : "";
        return field ? `${field}: ${msg}` : msg;
      })
      .filter((part): part is string => part !== null);
    if (parts.length > 0) return parts.join("; ");
  }
  return null;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      return formatDetail((body as { detail: unknown }).detail) ?? response.statusText;
    }
  } catch {
    /* non-JSON error body */
  }
  return response.statusText;
}

async function send(url: string, init?: RequestInit): Promise<Response> {
  const headers = new Headers(init?.headers);
  if (init?.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(url, { ...init, headers });
  if (!response.ok) {
    throw new ApiError(response.status, await errorDetail(response));
  }
  return response;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await send(url, init);
  return (await response.json()) as T;
}

async function requestVoid(url: string, init?: RequestInit): Promise<void> {
  await send(url, init);
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

export function updateChannel(id: string, payload: ChannelUpdate): Promise<Channel> {
  return request<Channel>(`/api/channels/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteChannel(id: string): Promise<void> {
  return requestVoid(`/api/channels/${id}`, { method: "DELETE" });
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

export function resetEtr290(id: string): Promise<Channel> {
  return request<Channel>(`/api/channels/${id}/etr290/reset`, { method: "POST" });
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

export function updateAsset(id: string, payload: AssetUpdate): Promise<Asset> {
  return request<Asset>(`/api/assets/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteAsset(id: string): Promise<void> {
  return requestVoid(`/api/assets/${id}`, { method: "DELETE" });
}

export function listRecordings(params: RecordingListParams = {}): Promise<Recording[]> {
  const query = new URLSearchParams();
  if (params.channel_id) query.set("channel_id", params.channel_id);
  if (params.status) query.set("status", params.status);
  if (params.limit) query.set("limit", String(params.limit));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return request<Recording[]>(`/api/recordings${suffix}`);
}

export function getRecording(id: string): Promise<Recording> {
  return request<Recording>(`/api/recordings/${id}`);
}

export function updateRecording(id: string, payload: RecordingUpdate): Promise<Recording> {
  return request<Recording>(`/api/recordings/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteRecording(id: string): Promise<void> {
  return requestVoid(`/api/recordings/${id}`, { method: "DELETE" });
}

export function listWorkers(): Promise<Worker[]> {
  return request<Worker[]>("/api/workers");
}
