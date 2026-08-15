import { Link, useParams } from "react-router-dom";
import { getAsset } from "../api/client";
import { HlsPlayer } from "../components/HlsPlayer";
import { usePoll } from "../hooks/usePoll";
import { formatDuration, formatWhen } from "../lib/format";

export function AssetDetailPage() {
  const { assetId } = useParams();
  const { data: asset, error } = usePoll(() => getAsset(assetId as string), 8000, assetId);

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

  return (
    <div className="space-y-6">
      <div>
        <Link to="/assets" className="text-xs text-slate-500 hover:text-slate-300">
          ← Assets
        </Link>
        <h1 className="mt-1 text-xl font-semibold">{asset.title}</h1>
        <p className="mt-1 text-sm text-slate-400">
          Channel{" "}
          <Link className="text-sky-400 hover:text-sky-300" to={`/channels/${asset.channel_id}`}>
            {asset.channel_name || asset.channel_id}
          </Link>
        </p>
      </div>

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
