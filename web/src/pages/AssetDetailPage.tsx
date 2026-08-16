import { FormEvent, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, deleteAsset, getAsset, updateAsset } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { HlsPlayer } from "../components/HlsPlayer";
import { usePoll } from "../hooks/usePoll";
import { formatDuration, formatWhen } from "../lib/format";

export function AssetDetailPage() {
  const navigate = useNavigate();
  const { assetId } = useParams();
  const { data: asset, error, reload } = usePoll(
    () => getAsset(assetId as string),
    8000,
    assetId,
  );
  const [editingTitle, setEditingTitle] = useState(false);
  const [title, setTitle] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  if (!assetId) return <p className="text-signal-err">Missing asset id</p>;
  if (error && !asset) return <p className="text-signal-err">{error}</p>;
  if (!asset) return <p className="text-slate-400">Loading…</p>;

  const playlist = asset.urls.proxy_playlist;
  const segmentCount =
    typeof asset.metadata.segment_count === "number" ? asset.metadata.segment_count : null;
  const mediaError =
    typeof asset.metadata.media_error === "string" ? asset.metadata.media_error : null;
  const proxyStatus =
    typeof asset.metadata.proxy === "string" ? asset.metadata.proxy : null;
  let proxyMessage = "HLS proxy not available";
  if (mediaError === "no_segments" || segmentCount === 0) {
    proxyMessage = "No media segments captured — start recording with a live source, then stop";
  } else if (mediaError === "master_not_a_file") {
    proxyMessage = "Master file missing on disk — proxy cannot be generated";
  } else if (proxyStatus === "failed") {
    proxyMessage = "HLS proxy generation failed during post-process";
  } else if (proxyStatus === "skipped") {
    proxyMessage = "HLS proxy was skipped for this asset";
  }

  const startEdit = () => {
    setTitle(asset.title);
    setEditingTitle(true);
    setActionError(null);
  };

  const saveTitle = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setActionError(null);
    try {
      await updateAsset(asset.id, { title: title.trim() });
      setEditingTitle(false);
      reload();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "Failed to update title");
    } finally {
      setBusy(false);
    }
  };

  const confirmDelete = async () => {
    setBusy(true);
    setActionError(null);
    try {
      await deleteAsset(asset.id);
      navigate("/assets");
    } catch (err) {
      setActionError(err instanceof ApiError ? err.detail : "Failed to delete asset");
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <Link to="/assets" className="text-xs text-slate-500 hover:text-slate-300">
            ← Assets
          </Link>
          {editingTitle ? (
            <form className="mt-2 flex flex-wrap items-center gap-2" onSubmit={saveTitle}>
              <input
                className="field max-w-md"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
              />
              <button className="btn-primary" disabled={busy}>
                Save
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => setEditingTitle(false)}
              >
                Cancel
              </button>
            </form>
          ) : (
            <div className="mt-1 flex items-center gap-3">
              <h1 className="text-xl font-semibold">{asset.title}</h1>
              <button className="btn-ghost" onClick={startEdit}>
                Edit title
              </button>
            </div>
          )}
          <p className="mt-1 text-sm text-slate-400">
            Channel{" "}
            <Link className="text-sky-400 hover:text-sky-300" to={`/channels/${asset.channel_id}`}>
              {asset.channel_name || asset.channel_id}
            </Link>
          </p>
        </div>
        <button className="btn-danger" disabled={busy} onClick={() => setDeleteOpen(true)}>
          Delete asset
        </button>
      </div>
      {actionError && <p className="text-sm text-signal-err">{actionError}</p>}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="panel overflow-hidden lg:col-span-2">
          {playlist ? (
            <HlsPlayer src={playlist} />
          ) : (
            <div className="flex aspect-video items-center justify-center px-6 text-center text-sm text-slate-500">
              {proxyMessage}
            </div>
          )}
        </div>
        <div className="space-y-4">
          {asset.urls.thumbnail && (
            <img
              src={asset.urls.thumbnail}
              alt=""
              className="panel w-full object-cover"
            />
          )}
          <dl className="panel space-y-3 p-4 text-sm">
            <Row label="Duration" value={formatDuration(asset.duration_ms)} />
            <Row
              label="Raster"
              value={asset.width && asset.height ? `${asset.width}×${asset.height}` : "—"}
            />
            <Row label="Video" value={asset.video_codec ?? "—"} />
            <Row label="Audio" value={asset.audio_codec ?? "—"} />
            <Row label="Created" value={formatWhen(asset.created_at)} />
          </dl>
          {asset.urls.master && (
            <a className="btn-primary w-full" href={asset.urls.master}>
              Download master
            </a>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={deleteOpen}
        title="Delete asset"
        message={`Delete "${asset.title}" and all associated media files? This cannot be undone.`}
        busy={busy}
        onCancel={() => setDeleteOpen(false)}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-slate-400">{label}</dt>
      <dd className="font-mono text-xs text-slate-200">{value}</dd>
    </div>
  );
}
