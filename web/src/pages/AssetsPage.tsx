import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { listAssets, listChannels } from "../api/client";
import { usePoll } from "../hooks/usePoll";
import { formatDuration, formatWhen } from "../lib/format";

export function AssetsPage() {
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [channelId, setChannelId] = useState("");
  const { data: channels } = usePoll(listChannels, 10_000);
  const { data: assets, error } = usePoll(
    () => listAssets({ q: submitted || undefined, channel_id: channelId || undefined }),
    3000,
    `${submitted}|${channelId}`,
  );

  const onSearch = (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(q.trim());
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
      {error && <p className="mb-3 text-sm text-signal-err">{error}</p>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {(assets ?? []).length === 0 && (
          <div className="panel col-span-full px-4 py-10 text-center text-slate-500">
            No assets cataloged yet.
          </div>
        )}
        {(assets ?? []).map((asset) => (
          <Link
            key={asset.id}
            to={`/assets/${asset.id}`}
            className="panel overflow-hidden transition hover:border-sky-700"
          >
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
                  {[asset.video_codec, asset.audio_codec].filter(Boolean).join(" / ") || "codec n/a"}
                </span>
                <span>{formatWhen(asset.created_at)}</span>
              </div>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
