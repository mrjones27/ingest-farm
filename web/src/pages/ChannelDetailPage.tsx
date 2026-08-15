import { Link, useParams } from "react-router-dom";
import { ApiError, getChannel, listAssets, startChannel, stopChannel } from "../api/client";
import { StatusBadge } from "../components/StatusBadge";
import { usePoll } from "../hooks/usePoll";
import { formatDuration, formatWhen } from "../lib/format";
import { useState } from "react";

export function ChannelDetailPage() {
  const { channelId } = useParams();
  const { data: channel, error, reload } = usePoll(
    () => getChannel(channelId as string),
    2000,
    channelId,
  );
  const { data: assets } = usePoll(
    () => listAssets({ channel_id: channelId }),
    4000,
    channelId,
  );
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  if (!channelId) return <p className="text-signal-err">Missing channel id</p>;
  if (error && !channel) return <p className="text-signal-err">{error}</p>;
  if (!channel) return <p className="text-slate-400">Loading…</p>;

  const recording = channel.status === "recording" || channel.status === "starting";

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
        {recording ? (
          <button className="btn-danger" disabled={busy} onClick={() => void run(stopChannel)}>
            Stop recording
          </button>
        ) : (
          <button className="btn-live" disabled={busy} onClick={() => void run(startChannel)}>
            Start recording
          </button>
        )}
      </div>
      {actionError && <p className="text-sm text-signal-err">{actionError}</p>}

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
                  <td className="px-4 py-3 font-mono text-xs">{formatDuration(asset.duration_ms)}</td>
                  <td className="px-4 py-3 text-slate-400">{formatWhen(asset.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
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
