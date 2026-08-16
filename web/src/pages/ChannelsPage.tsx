import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  connectChannel,
  createChannel,
  disconnectChannel,
  listChannels,
  startRecording,
  stopRecording,
} from "../api/client";
import type { Channel, ChannelCreate, PipelineProfile, SourceProtocol } from "../api/types";
import { LiveThumb } from "../components/LiveThumb";
import { SrtStatsPanel } from "../components/SrtStatsPanel";
import { StatusBadge } from "../components/StatusBadge";
import { usePoll } from "../hooks/usePoll";
import { formatWhen } from "../lib/format";

const PROTOCOLS: SourceProtocol[] = ["srt", "udp", "rtmp", "hls", "file"];
const PROFILES: PipelineProfile[] = ["ts_passthrough", "transcode_remux"];

const URI_HINT: Record<SourceProtocol, string> = {
  srt: "srt://0.0.0.0:9000?mode=listener",
  udp: "udp://0.0.0.0:5000",
  rtmp: "rtmp://example.com/live/stream",
  hls: "https://example.com/live/index.m3u8",
  file: "/path/to/source.ts",
};

const LIVE = new Set([
  "connecting",
  "connected",
  "recording",
  "starting",
  "stopping",
  "disconnecting",
]);

function ChannelActions({
  channel,
  busyId,
  onConnect,
  onDisconnect,
  onRecord,
  onStopRecord,
}: {
  channel: Channel;
  busyId: string | null;
  onConnect: (id: string) => void;
  onDisconnect: (id: string) => void;
  onRecord: (id: string) => void;
  onStopRecord: (id: string) => void;
}) {
  const busy = busyId === channel.id;
  const live = LIVE.has(channel.status);
  const recording = channel.status === "recording" || channel.status === "starting";
  const canRecord =
    channel.status === "connected" ||
    channel.status === "connecting" ||
    channel.status === "stopping";

  return (
    <div className="flex flex-wrap justify-end gap-2">
      {!live ? (
        <button className="btn-primary" disabled={busy} onClick={() => onConnect(channel.id)}>
          Connect
        </button>
      ) : (
        <button className="btn-ghost" disabled={busy} onClick={() => onDisconnect(channel.id)}>
          Disconnect
        </button>
      )}
      {recording ? (
        <button className="btn-danger" disabled={busy} onClick={() => onStopRecord(channel.id)}>
          Stop
        </button>
      ) : (
        <button
          className="btn-live"
          disabled={busy || !canRecord}
          onClick={() => onRecord(channel.id)}
        >
          Record
        </button>
      )}
    </div>
  );
}

function CreateChannelModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [protocol, setProtocol] = useState<SourceProtocol>("udp");
  const [uri, setUri] = useState(URI_HINT.udp);
  const [caps, setCaps] = useState("video/mpegts");
  const [transport, setTransport] = useState("mpegts");
  const [profile, setProfile] = useState<PipelineProfile>("ts_passthrough");
  const [segmentDuration, setSegmentDuration] = useState(3600);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  if (!open) return null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    const config: Record<string, unknown> = {};
    if (protocol === "udp") {
      if (caps) config.caps = caps;
      if (transport) config.transport = transport;
    }
    const payload: ChannelCreate = {
      name: name.trim(),
      source: { protocol, uri: uri.trim(), config },
      pipeline: {
        profile,
        segment_duration_sec: segmentDuration,
        video: {},
        audio: {},
      },
    };
    try {
      await createChannel(payload);
      onCreated();
      onClose();
      setName("");
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to create channel");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-black/70 p-6 pt-24">
      <form className="panel w-full max-w-lg p-5 shadow-xl" onSubmit={submit}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-medium">New channel</h2>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="space-y-3">
          <div>
            <label className="label" htmlFor="ch-name">
              Name
            </label>
            <input
              id="ch-name"
              className="field"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label" htmlFor="ch-protocol">
                Protocol
              </label>
              <select
                id="ch-protocol"
                className="field"
                value={protocol}
                onChange={(e) => {
                  const next = e.target.value as SourceProtocol;
                  setProtocol(next);
                  setUri(URI_HINT[next]);
                }}
              >
                {PROTOCOLS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label" htmlFor="ch-profile">
                Pipeline
              </label>
              <select
                id="ch-profile"
                className="field"
                value={profile}
                onChange={(e) => setProfile(e.target.value as PipelineProfile)}
              >
                {PROFILES.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="label" htmlFor="ch-uri">
              Source URI
            </label>
            <input
              id="ch-uri"
              className="field font-mono"
              required
              value={uri}
              onChange={(e) => setUri(e.target.value)}
            />
          </div>
          {protocol === "udp" && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label" htmlFor="ch-caps">
                  Caps
                </label>
                <input
                  id="ch-caps"
                  className="field font-mono"
                  value={caps}
                  onChange={(e) => setCaps(e.target.value)}
                />
              </div>
              <div>
                <label className="label" htmlFor="ch-transport">
                  Transport
                </label>
                <select
                  id="ch-transport"
                  className="field"
                  value={transport}
                  onChange={(e) => setTransport(e.target.value)}
                >
                  <option value="mpegts">mpegts</option>
                  <option value="rtp-h264">rtp-h264</option>
                  <option value="rtp-mp2t">rtp-mp2t</option>
                </select>
              </div>
            </div>
          )}
          <div>
            <label className="label" htmlFor="ch-seg">
              Segment duration (sec)
            </label>
            <input
              id="ch-seg"
              className="field"
              type="number"
              min={1}
              value={segmentDuration}
              onChange={(e) => setSegmentDuration(Number(e.target.value))}
            />
          </div>
          {error && <p className="text-sm text-signal-err">{error}</p>}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" disabled={saving}>
            {saving ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}

export function ChannelsPage() {
  const { data, error, reload } = usePoll(listChannels, 2000);
  const [open, setOpen] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const channels = data ?? [];

  const run = async (id: string, fn: (id: string) => Promise<Channel>) => {
    setBusyId(id);
    setActionError(null);
    try {
      await fn(id);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "Action failed");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Channels</h1>
          <p className="text-sm text-slate-400">
            Connect a source for live preview, then record when you need a capture.
          </p>
        </div>
        <button className="btn-primary" onClick={() => setOpen(true)}>
          New channel
        </button>
      </div>
      {(error || actionError) && (
        <p className="mb-3 text-sm text-signal-err">{actionError ?? error}</p>
      )}
      <div className="panel overflow-hidden">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-700 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-2 font-medium">Preview</th>
              <th className="px-4 py-2 font-medium">Name</th>
              <th className="px-4 py-2 font-medium">Protocol</th>
              <th className="px-4 py-2 font-medium">URI</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">SRT</th>
              <th className="px-4 py-2 font-medium">Created</th>
              <th className="px-4 py-2 font-medium" />
            </tr>
          </thead>
          <tbody>
            {channels.length === 0 && (
              <tr>
                <td className="px-4 py-8 text-center text-slate-500" colSpan={8}>
                  No channels yet. Create one to start ingest.
                </td>
              </tr>
            )}
            {channels.map((channel) => (
              <tr key={channel.id} className="border-t border-ink-600">
                <td className="px-4 py-3">
                  <LiveThumb
                    url={channel.urls?.thumbnail}
                    alt={`${channel.name} preview`}
                    className="h-14 w-24 rounded"
                  />
                </td>
                <td className="px-4 py-3">
                  <Link
                    className="font-medium text-sky-400 hover:text-sky-300"
                    to={`/channels/${channel.id}`}
                  >
                    {channel.name}
                  </Link>
                </td>
                <td className="px-4 py-3 font-mono text-xs uppercase">{channel.source.protocol}</td>
                <td className="max-w-xs truncate px-4 py-3 font-mono text-xs text-slate-400">
                  {channel.source.uri}
                </td>
                <td className="px-4 py-3">
                  <StatusBadge status={channel.status} />
                </td>
                <td className="whitespace-nowrap px-4 py-3">
                  {channel.source.protocol === "srt" ? (
                    <SrtStatsPanel stats={channel.stats} compact />
                  ) : (
                    <span className="text-xs text-slate-600">—</span>
                  )}
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-slate-400">
                  {formatWhen(channel.created_at)}
                </td>
                <td className="px-4 py-3 text-right">
                  <ChannelActions
                    channel={channel}
                    busyId={busyId}
                    onConnect={(id) => void run(id, connectChannel)}
                    onDisconnect={(id) => void run(id, disconnectChannel)}
                    onRecord={(id) => void run(id, startRecording)}
                    onStopRecord={(id) => void run(id, stopRecording)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <CreateChannelModal open={open} onClose={() => setOpen(false)} onCreated={reload} />
    </div>
  );
}
