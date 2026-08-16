import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, deleteAsset, listAssets, listChannels } from "../api/client";
import type { Asset } from "../api/types";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { usePoll } from "../hooks/usePoll";
import { formatDuration, formatWhen } from "../lib/format";

export function AssetsPage() {
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [channelId, setChannelId] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Asset | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const { data: channels } = usePoll(listChannels, 10_000);
  const { data: assets, error, reload } = usePoll(
    () => listAssets({ q: submitted || undefined, channel_id: channelId || undefined }),
    3000,
    `${submitted}|${channelId}`,
  );

  const onSearch = (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(q.trim());
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setBusy(true);
    setActionError(null);
    try {
      await deleteAsset(deleteTarget.id);
      setDeleteTarget(null);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "Failed to delete asset");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-4">
        <h1 className="text-xl font-semibold">Assets</h1>
        <p className="text-sm text-slate-400">Browse recorded sessions, proxies, and thumbnails.</p>
      </div>
      <form className="mb-4 flex flex-wrap gap-3" onSubmit={onSearch}>
        <input
          className="field max-w-sm"
          placeholder="Search titles"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
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
        <button className="btn-ghost" type="submit">
          Search
        </button>
      </form>
      {(error || actionError) && (
        <p className="mb-3 text-sm text-signal-err">{actionError ?? error}</p>
      )}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {(assets ?? []).length === 0 && (
          <div className="panel col-span-full px-4 py-10 text-center text-slate-500">
            No assets cataloged yet.
          </div>
        )}
        {(assets ?? []).map((asset) => (
          <div key={asset.id} className="panel overflow-hidden">
            <Link to={`/assets/${asset.id}`} className="block transition hover:border-sky-700">
              <div className="aspect-video bg-ink-900">
                {asset.urls.thumbnail ? (
                  <img
                    src={asset.urls.thumbnail}
                    alt=""
                    className="h-full w-full object-cover"
                  />
                ) : (
                  <div className="flex h-full items-center justify-center text-xs text-slate-600">
                    No thumbnail
                  </div>
                )}
              </div>
              <div className="space-y-1 p-3">
                <div className="truncate font-medium">{asset.title}</div>
                <div className="flex justify-between text-xs text-slate-400">
                  <span>{asset.channel_name || "—"}</span>
                  <span className="font-mono">{formatDuration(asset.duration_ms)}</span>
                </div>
                <div className="flex justify-between text-xs text-slate-500">
                  <span>
                    {[asset.video_codec, asset.audio_codec].filter(Boolean).join(" / ") ||
                      "codec n/a"}
                  </span>
                  <span>{formatWhen(asset.created_at)}</span>
                </div>
              </div>
            </Link>
            <div className="border-t border-ink-600 px-3 py-2 text-right">
              <button className="btn-danger" onClick={() => setDeleteTarget(asset)}>
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete asset"
        message={`Delete "${deleteTarget?.title}" and all associated media files? This cannot be undone.`}
        busy={busy}
        onCancel={() => setDeleteTarget(null)}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}
