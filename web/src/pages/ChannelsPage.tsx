import { useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  connectChannel,
  deleteChannel,
  disconnectChannel,
  listChannels,
  startRecording,
  stopRecording,
} from "../api/client";
import type { Channel } from "../api/types";
import { ChannelFormModal } from "../components/ChannelFormModal";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { LiveThumb } from "../components/LiveThumb";
import { SrtStatsPanel } from "../components/SrtStatsPanel";
import { StatusBadge } from "../components/StatusBadge";
import { usePoll } from "../hooks/usePoll";
import { formatWhen } from "../lib/format";

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

function ChannelActions({
  channel,
  busyId,
  onConnect,
  onDisconnect,
  onRecord,
  onStopRecord,
  onEdit,
  onDelete,
}: {
  channel: Channel;
  busyId: string | null;
  onConnect: (id: string) => void;
  onDisconnect: (id: string) => void;
  onRecord: (id: string) => void;
  onStopRecord: (id: string) => void;
  onEdit: (channel: Channel) => void;
  onDelete: (channel: Channel) => void;
}) {
  const busy = busyId === channel.id;
  const live = LIVE.has(channel.status);
  const canModify = IDLE.has(channel.status);
  const recording = STOPPABLE.has(channel.status);
  const canRecord = RECORDABLE.has(channel.status);

  return (
    <div className="flex flex-wrap justify-end gap-2">
      <button
        className="btn-ghost"
        disabled={busy || !canModify}
        onClick={() => onEdit(channel)}
      >
        Edit
      </button>
      <button
        className="btn-danger"
        disabled={busy || !canModify}
        onClick={() => onDelete(channel)}
      >
        Delete
      </button>
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

export function ChannelsPage() {
  const { data, error, reload } = usePoll(listChannels, 2000);
  const [createOpen, setCreateOpen] = useState(false);
  const [editChannel, setEditChannel] = useState<Channel | null>(null);
  const [deleteChannelTarget, setDeleteChannelTarget] = useState<Channel | null>(null);
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

  const confirmDelete = async () => {
    if (!deleteChannelTarget) return;
    setBusyId(deleteChannelTarget.id);
    setActionError(null);
    try {
      await deleteChannel(deleteChannelTarget.id);
      setDeleteChannelTarget(null);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "Failed to delete channel");
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
        <button className="btn-primary" onClick={() => setCreateOpen(true)}>
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
                    className="h-14 w-24 rounded object-cover"
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
                    onEdit={setEditChannel}
                    onDelete={setDeleteChannelTarget}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <ChannelFormModal
        open={createOpen}
        mode="create"
        onClose={() => setCreateOpen(false)}
        onSaved={reload}
      />
      <ChannelFormModal
        open={editChannel !== null}
        mode="edit"
        channel={editChannel ?? undefined}
        onClose={() => setEditChannel(null)}
        onSaved={reload}
      />
      <ConfirmDialog
        open={deleteChannelTarget !== null}
        title="Delete channel"
        message={`Delete "${deleteChannelTarget?.name}" and all recordings with media files? This cannot be undone.`}
        busy={busyId === deleteChannelTarget?.id}
        onCancel={() => setDeleteChannelTarget(null)}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}
