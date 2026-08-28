import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ApiError,
  connectChannel,
  deleteChannel,
  deleteRecording,
  disconnectChannel,
  getChannel,
  listAssets,
  listRecordings,
  startRecording,
  stopRecording,
} from "../api/client";
import { ChannelFormModal } from "../components/ChannelFormModal";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { Etr290Panel } from "../components/Etr290Panel";
import { LiveThumb } from "../components/LiveThumb";
import { SrtStatsPanel } from "../components/SrtStatsPanel";
import { StatusBadge } from "../components/StatusBadge";
import { usePoll } from "../hooks/usePoll";
import { formatDuration, formatWhen } from "../lib/format";

const LIVE = new Set([
  "connecting",
  "connected",
  "recording",
  "starting",
  "stopping",
  "disconnecting",
]);

const IDLE = new Set(["idle", "error"]);

// Mirrors Scheduler.stop_recording / start_recording: a stop is still valid
// while stopping, and a record request is not.
const STOPPABLE = new Set(["recording", "starting", "stopping"]);
const RECORDABLE = new Set(["connected", "connecting"]);

export function ChannelDetailPage() {
  const navigate = useNavigate();
  const { channelId } = useParams();
  const { data: channel, error, reload } = usePoll(
    () => getChannel(channelId as string),
    1000,
    channelId,
  );
  const { data: assets, error: assetsError } = usePoll(
    () => listAssets({ channel_id: channelId }),
    4000,
    channelId,
  );
  const {
    data: recordings,
    error: recordingsError,
    reload: reloadRecordings,
  } = usePoll(() => listRecordings({ channel_id: channelId }), 4000, channelId);
  const [busy, setBusy] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteRecordingId, setDeleteRecordingId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  if (!channelId) return <p className="text-signal-err">Missing channel id</p>;
  if (error && !channel) return <p className="text-signal-err">{error}</p>;
  if (!channel) return <p className="text-slate-400">Loading…</p>;

  const live = LIVE.has(channel.status);
  const canModify = IDLE.has(channel.status);
  const recording = STOPPABLE.has(channel.status);
  const canRecord = RECORDABLE.has(channel.status);

  const run = async (fn: (id: string) => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try {
      await fn(channel.id);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "Action failed");
    } finally {
      setBusy(false);
    }
  };

  const confirmDeleteChannel = async () => {
    setBusy(true);
    setActionError(null);
    try {
      await deleteChannel(channel.id);
      navigate("/");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "Failed to delete channel");
      setBusy(false);
    }
  };

  const confirmDeleteRecording = async () => {
    if (!deleteRecordingId) return;
    setBusy(true);
    setActionError(null);
    try {
      await deleteRecording(deleteRecordingId);
      setDeleteRecordingId(null);
      reloadRecordings();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "Failed to delete recording");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link to="/" className="text-xs text-slate-500 hover:text-slate-300">
            ← Channels
          </Link>
          <h1 className="mt-1 text-xl font-semibold">{channel.name}</h1>
          <div className="mt-2">
            <StatusBadge status={channel.status} />
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            className="btn-ghost"
            disabled={busy || !canModify}
            onClick={() => setEditOpen(true)}
          >
            Edit
          </button>
          <button
            className="btn-danger"
            disabled={busy || !canModify}
            onClick={() => setDeleteOpen(true)}
          >
            Delete
          </button>
          {!live ? (
            <button className="btn-primary" disabled={busy} onClick={() => void run(connectChannel)}>
              Connect
            </button>
          ) : (
            <button
              className="btn-ghost"
              disabled={busy}
              onClick={() => void run(disconnectChannel)}
            >
              Disconnect
            </button>
          )}
          {recording ? (
            <button className="btn-danger" disabled={busy} onClick={() => void run(stopRecording)}>
              Stop recording
            </button>
          ) : (
            <button
              className="btn-live"
              disabled={busy || !canRecord}
              onClick={() => void run(startRecording)}
            >
              Record
            </button>
          )}
        </div>
      </div>
      {actionError && <p className="text-sm text-signal-err">{actionError}</p>}
      {/* Never present a stale status as current runtime state. */}
      {error && (
        <p className="text-sm text-signal-warn">
          Status refresh failed ({error}) — showing last known state.
        </p>
      )}
      {(assetsError || recordingsError) && (
        <p className="text-sm text-signal-warn">
          Recordings and assets may be out of date ({recordingsError ?? assetsError}).
        </p>
      )}

      <section className="panel overflow-hidden">
        <LiveThumb
          url={channel.urls?.thumbnail}
          alt={`${channel.name} live preview`}
          className="aspect-video w-full max-h-80"
        />
      </section>

      {channel.source.protocol === "srt" && live && (
        <section className="panel p-4">
          <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-slate-400">
            SRT stats
          </h2>
          <SrtStatsPanel stats={channel.stats} />
        </section>
      )}

      {channel.source.protocol === "srt" && live && (
        <section className="panel p-4">
          <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-slate-400">
            ETR 290 (transport stream)
          </h2>
          <Etr290Panel
            receiving={Boolean(channel.stats?.receiving)}
            stats={channel.stats}
          />
        </section>
      )}

      <section className="panel grid gap-4 p-4 sm:grid-cols-2">
        <Field label="Protocol" value={channel.source.protocol} mono />
        <Field label="Source URI" value={channel.source.uri} mono />
        <Field label="Pipeline" value={channel.pipeline.profile} mono />
        <Field label="Segment duration" value={`${channel.pipeline.segment_duration_sec}s`} />
        <Field label="Container" value={channel.output.container} />
        <Field label="Created" value={formatWhen(channel.created_at)} />
        {Object.keys(channel.source.config).length > 0 && (
          <div className="sm:col-span-2">
            <div className="label">Source config</div>
            <pre className="overflow-x-auto rounded-md bg-ink-900 p-3 font-mono text-xs text-slate-300">
              {JSON.stringify(channel.source.config, null, 2)}
            </pre>
          </div>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-slate-400">
          Recordings
        </h2>
        <div className="panel overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="bg-ink-700 text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-4 py-2 font-medium">Started</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Segments</th>
                <th className="px-4 py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {(recordings ?? []).length === 0 && (
                <tr>
                  <td className="px-4 py-6 text-slate-500" colSpan={4}>
                    No recordings for this channel yet.
                  </td>
                </tr>
              )}
              {(recordings ?? []).map((rec) => (
                <tr key={rec.id} className="border-t border-ink-600">
                  <td className="px-4 py-3 text-slate-400">{formatWhen(rec.started_at)}</td>
                  <td className="px-4 py-3">
                    <StatusBadge status={rec.status} />
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{rec.segment_count}</td>
                  <td className="px-4 py-3 text-right">
                    <button
                      className="btn-danger"
                      disabled={busy || rec.status === "recording"}
                      onClick={() => setDeleteRecordingId(rec.id)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-slate-400">
          Assets
        </h2>
        <div className="panel overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="bg-ink-700 text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-4 py-2 font-medium">Title</th>
                <th className="px-4 py-2 font-medium">Duration</th>
                <th className="px-4 py-2 font-medium">Created</th>
              </tr>
            </thead>
            <tbody>
              {(assets ?? []).length === 0 && (
                <tr>
                  <td className="px-4 py-6 text-slate-500" colSpan={3}>
                    No assets for this channel yet.
                  </td>
                </tr>
              )}
              {(assets ?? []).map((asset) => (
                <tr key={asset.id} className="border-t border-ink-600">
                  <td className="px-4 py-3">
                    <Link className="text-sky-400 hover:text-sky-300" to={`/assets/${asset.id}`}>
                      {asset.title}
                    </Link>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">
                    {formatDuration(asset.duration_ms)}
                  </td>
                  <td className="px-4 py-3 text-slate-400">{formatWhen(asset.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <ChannelFormModal
        open={editOpen}
        mode="edit"
        channel={channel}
        onClose={() => setEditOpen(false)}
        onSaved={reload}
      />
      <ConfirmDialog
        open={deleteOpen}
        title="Delete channel"
        message={`Delete "${channel.name}" and all recordings with media files? This cannot be undone.`}
        busy={busy}
        onCancel={() => setDeleteOpen(false)}
        onConfirm={() => void confirmDeleteChannel()}
      />
      <ConfirmDialog
        open={deleteRecordingId !== null}
        title="Delete recording"
        message="Delete this recording and all associated media files? This cannot be undone."
        busy={busy}
        onCancel={() => setDeleteRecordingId(null)}
        onConfirm={() => void confirmDeleteRecording()}
      />
    </div>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div>
      <div className="label">{label}</div>
      <div className={mono ? "break-all font-mono text-sm" : "text-sm"}>{value}</div>
    </div>
  );
}
