/** Compact live SRT metrics from worker-published Gst stats. */

function fmtRate(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "—";
  if (n >= 10) return n.toFixed(1);
  return n.toFixed(2);
}

function fmtMs(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "—";
  return n < 10 ? n.toFixed(2) : n.toFixed(1);
}

function fmtInt(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString();
}

function Stat({
  label,
  value,
  unit,
}: {
  label: string;
  value: string;
  unit?: string;
}) {
  return (
    <div className="min-w-0">
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="font-mono text-sm text-slate-100">
        {value}
        {unit && value !== "—" && (
          <span className="ml-1 text-xs text-slate-500">{unit}</span>
        )}
      </div>
    </div>
  );
}

export function SrtStatsPanel({
  stats,
  compact = false,
}: {
  stats: Record<string, unknown> | null | undefined;
  compact?: boolean;
}) {
  if (!stats || !stats.available) {
    if (compact) return <span className="text-xs text-slate-600">—</span>;
    return (
      <p className="text-sm text-slate-500">
        SRT stats appear once the socket is connected and receiving.
      </p>
    );
  }

  const rate =
    stats["receive-rate-mbps"] ?? stats["send-rate-mbps"];
  const lost =
    stats["packets-received-lost"] ?? stats["packets-sent-lost"];

  if (compact) {
    return (
      <span className="font-mono text-xs text-slate-300">
        {fmtMs(stats["rtt-ms"])} ms · {fmtRate(rate)} Mb/s
        {lost != null && Number(lost) > 0 ? ` · lost ${fmtInt(lost)}` : ""}
      </span>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
      <Stat label="RTT" value={fmtMs(stats["rtt-ms"])} unit="ms" />
      <Stat label="Est. path" value={fmtRate(stats["bandwidth-mbps"])} unit="Mb/s" />
      <Stat
        label="Recv rate"
        value={fmtRate(stats["receive-rate-mbps"] ?? stats["send-rate-mbps"])}
        unit="Mb/s"
      />
      <Stat
        label="Latency"
        value={fmtInt(stats["negotiated-latency-ms"])}
        unit="ms"
      />
      <Stat label="Lost (total)" value={fmtInt(lost)} />
      <Stat
        label="NACKs sent"
        value={fmtInt(stats["packet-nack-sent"] ?? stats["packets-retransmitted"] ?? stats["packet-nack-received"])}
      />
    </div>
  );
}
