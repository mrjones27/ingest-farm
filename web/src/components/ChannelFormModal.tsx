import { FormEvent, useEffect, useState } from "react";
import { ApiError, createChannel, updateChannel } from "../api/client";
import type { Channel, ChannelCreate, PipelineProfile, SourceProtocol } from "../api/types";

const PROTOCOLS: SourceProtocol[] = ["srt", "udp", "rtmp", "hls", "file"];
const PROFILES: PipelineProfile[] = ["ts_passthrough"];

const URI_HINT: Record<SourceProtocol, string> = {
  srt: "srt://0.0.0.0:9000?mode=listener",
  udp: "udp://0.0.0.0:5000",
  rtmp: "rtmp://example.com/live/stream",
  hls: "https://example.com/live/index.m3u8",
  file: "/path/to/source.ts",
};

export function ChannelFormModal({
  open,
  mode,
  channel,
  onClose,
  onSaved,
}: {
  open: boolean;
  mode: "create" | "edit";
  channel?: Channel;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [protocol, setProtocol] = useState<SourceProtocol>("udp");
  const [uri, setUri] = useState(URI_HINT.udp);
  const [caps, setCaps] = useState("video/mpegts");
  const [transport, setTransport] = useState("mpegts");
  const [profile, setProfile] = useState<PipelineProfile>("ts_passthrough");
  const [segmentDuration, setSegmentDuration] = useState(3600);
  const [enabled, setEnabled] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (mode === "edit" && channel) {
      setName(channel.name);
      setProtocol(channel.source.protocol);
      setUri(channel.source.uri);
      setCaps(String(channel.source.config.caps ?? "video/mpegts"));
      setTransport(String(channel.source.config.transport ?? "mpegts"));
      setProfile(channel.pipeline.profile);
      setSegmentDuration(channel.pipeline.segment_duration_sec);
      setEnabled(channel.enabled);
    } else {
      setName("");
      setProtocol("udp");
      setUri(URI_HINT.udp);
      setCaps("video/mpegts");
      setTransport("mpegts");
      setProfile("ts_passthrough");
      setSegmentDuration(3600);
      setEnabled(true);
    }
    setError(null);
  }, [open, mode, channel]);

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
      if (mode === "edit" && channel) {
        await updateChannel(channel.id, { ...payload, enabled });
      } else {
        await createChannel(payload);
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : "Failed to save channel");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-black/70 p-6 pt-24">
      <form className="panel w-full max-w-lg p-5 shadow-xl" onSubmit={submit}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-medium">
            {mode === "edit" ? "Edit channel" : "New channel"}
          </h2>
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
                  if (mode === "create") setUri(URI_HINT[next]);
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
          {mode === "edit" && (
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              Enabled
            </label>
          )}
          {error && <p className="text-sm text-signal-err">{error}</p>}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn-primary" disabled={saving}>
            {saving ? "Saving…" : mode === "edit" ? "Save" : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}
