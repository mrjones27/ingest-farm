import { Link } from "react-router-dom";
import { listWorkers } from "../api/client";
import { usePoll } from "../hooks/usePoll";
import { isStaleHeartbeat, timeAgo } from "../lib/format";

export function WorkersPage() {
  const { data, error } = usePoll(listWorkers, 2000);
  const workers = data ?? [];

  return (
    <div>
      <div className="mb-4">
        <h1 className="text-xl font-semibold">Workers</h1>
        <p className="text-sm text-slate-400">Ingest recorder capacity and heartbeats.</p>
      </div>
      {error && <p className="mb-3 text-sm text-signal-err">{error}</p>}
      <div className="grid gap-4 md:grid-cols-2">
        {workers.length === 0 && (
          <div className="panel col-span-full px-4 py-10 text-center text-slate-500">
            No workers registered.
          </div>
        )}
        {workers.map((worker) => {
          const used = worker.active_channels.length;
          const pct = worker.capacity > 0 ? Math.min(100, (used / worker.capacity) * 100) : 0;
          const stale = isStaleHeartbeat(worker.last_heartbeat);
          return (
            <div key={worker.id} className="panel p-4">
              <div className="mb-3 flex items-start justify-between">
                <div>
                  <div className="font-medium">{worker.hostname}</div>
                  <div className="font-mono text-xs text-slate-500">{worker.id}</div>
                </div>
                <span
                  className={`rounded-full px-2 py-0.5 text-xs ${
                    stale ? "bg-signal-err/15 text-signal-err" : "bg-signal-live/15 text-signal-live"
                  }`}
                >
                  {stale ? "stale" : "online"}
                </span>
              </div>
              <div className="mb-1 flex justify-between text-xs text-slate-400">
                <span>
                  {used} / {worker.capacity} channels
                </span>
                <span>heartbeat {timeAgo(worker.last_heartbeat)}</span>
              </div>
              <div className="h-2 overflow-hidden rounded bg-ink-900">
                <div className="h-full bg-sky-600" style={{ width: `${pct}%` }} />
              </div>
              {used > 0 && (
                <ul className="mt-3 space-y-1 text-xs">
                  {worker.active_channels.map((id) => (
                    <li key={id}>
                      <Link className="font-mono text-sky-400 hover:text-sky-300" to={`/channels/${id}`}>
                        {id}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
