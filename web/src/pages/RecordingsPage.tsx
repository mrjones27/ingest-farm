import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import {
  ApiError,
  deleteRecording,
  listChannels,
  listRecordings,
  updateRecording,
} from "../api/client";
import type { Recording, RecordingStatus } from "../api/types";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { StatusBadge } from "../components/StatusBadge";
import { usePoll } from "../hooks/usePoll";
import { formatWhen } from "../lib/format";

const STATUSES: RecordingStatus[] = ["recording", "completed", "failed"];

export function RecordingsPage() {
  const [channelId, setChannelId] = useState("");
  const [status, setStatus] = useState<RecordingStatus | "">("");
  const [editing, setEditing] = useState<Recording | null>(null);
  const [metadataJson, setMetadataJson] = useState("{}");
  const [deleteTarget, setDeleteTarget] = useState<Recording | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const { data: channels } = usePoll(listChannels, 10_000);
  const { data: recordings, error, reload } = usePoll(
    () =>
      listRecordings({
        channel_id: channelId || undefined,
        status: status || undefined,
      }),
    3000,
    `${channelId}|${status}`,
  );

  const channelName = (id: string) =>
    (channels ?? []).find((channel) => channel.id === id)?.name ?? id;

  const openEdit = (recording: Recording) => {
    setEditing(recording);
    setMetadataJson(JSON.stringify(recording.metadata ?? {}, null, 2));
    setActionError(null);
  };

  const saveMetadata = async (event: FormEvent) => {
    event.preventDefault();
    if (!editing) return;
    setBusy(true);
    setActionError(null);
    try {
      const metadata = JSON.parse(metadataJson) as Record<string, unknown>;
      await updateRecording(editing.id, { metadata });
      setEditing(null);
      reload();
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.detail : "Failed to update recording metadata",
      );
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setBusy(true);
    setActionError(null);
    try {
      await deleteRecording(deleteTarget.id);
      setDeleteTarget(null);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "Failed to delete recording");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-4">
        <h1 className="text-xl font-semibold">Recordings</h1>
        <p className="text-sm text-slate-400">
          All capture sessions, including failed and in-progress recordings.
        </p>
      </div>

      <div className="mb-4 flex flex-wrap gap-3">
        <select
          className="field max-w-xs"
          value={channelId}
          onChange={(e) => setChannelId(e.target.value)}
        >
          <option value="">All channels</option>
          {(channels ?? []).map((channel) => (
            <option key={channel.id} value={channel.id}>
              {channel.name}
            </option>
          ))}
        </select>
        <select
          className="field max-w-xs"
          value={status}
          onChange={(e) => setStatus(e.target.value as RecordingStatus | "")}
        >
          <option value="">All statuses</option>
          {STATUSES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </div>

      {(error || actionError) && (
        <p className="mb-3 text-sm text-signal-err">{actionError ?? error}</p>
      )}

      <div className="panel overflow-hidden">
        <table className="w-full text-left text-sm">
          <thead className="bg-ink-700 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-2 font-medium">Channel</th>
              <th className="px-4 py-2 font-medium">Started</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Segments</th>
              <th className="px-4 py-2 font-medium" />
            </tr>
          </thead>
          <tbody>
            {(recordings ?? []).length === 0 && (
              <tr>
                <td className="px-4 py-8 text-center text-slate-500" colSpan={5}>
                  No recordings found.
                </td>
              </tr>
            )}
            {(recordings ?? []).map((recording) => (
              <tr key={recording.id} className="border-t border-ink-600">
                <td className="px-4 py-3">
                  <Link
                    className="text-sky-400 hover:text-sky-300"
                    to={`/channels/${recording.channel_id}`}
                  >
                    {channelName(recording.channel_id)}
                  </Link>
                </td>
                <td className="px-4 py-3 text-slate-400">{formatWhen(recording.started_at)}</td>
                <td className="px-4 py-3">
                  <StatusBadge status={recording.status} />
                </td>
                <td className="px-4 py-3 font-mono text-xs">{recording.segment_count}</td>
                <td className="px-4 py-3 text-right">
                  <div className="flex justify-end gap-2">
                    <button
                      className="btn-ghost"
                      disabled={recording.status === "recording"}
                      onClick={() => openEdit(recording)}
                    >
                      Edit
                    </button>
                    <button
                      className="btn-danger"
                      disabled={recording.status === "recording"}
                      onClick={() => setDeleteTarget(recording)}
                    >
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && (
        <div className="fixed inset-0 z-40 flex items-start justify-center bg-black/70 p-6 pt-24">
          <form className="panel w-full max-w-lg p-5 shadow-xl" onSubmit={saveMetadata}>
            <h2 className="text-lg font-medium">Edit recording metadata</h2>
            <p className="mt-1 text-sm text-slate-400">
              {channelName(editing.channel_id)} — {formatWhen(editing.started_at)}
            </p>
            <textarea
              className="field mt-4 min-h-48 font-mono text-xs"
              value={metadataJson}
              onChange={(e) => setMetadataJson(e.target.value)}
            />
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className="btn-ghost" onClick={() => setEditing(null)}>
                Cancel
              </button>
              <button className="btn-primary" disabled={busy}>
                {busy ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete recording"
        message="Delete this recording and all associated media files? This cannot be undone."
        busy={busy}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}
