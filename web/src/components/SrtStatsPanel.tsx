/** Compact live SRT metrics from worker-published Gst / probe stats. */

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
  if (!stats || stats.available !== true) {
    if (compact) return <span className="text-xs text-slate-600">—</span>;
    return (
      <p className="text-sm text-slate-500">
        SRT stats appear once the socket is connected and receiving.
      </p>
    );
  }

  const rate = stats["receive-rate-mbps"] ?? stats["send-rate-mbps"];
  const lost = stats["packets-received-lost"] ?? stats["packets-sent-lost"];
  const hasRtt = stats["rtt-ms"] != null;
  const state = stats.receiving ? "receiving" : "listening";

  if (compact) {
    const parts = [state];
    if (rate != null && Number.isFinite(Number(rate))) {
      parts.push(`${fmtRate(rate)} Mb/s`);
    }
    if (hasRtt) {
      parts.push(`${fmtMs(stats["rtt-ms"])} ms`);
    }
    if (lost != null && Number(lost) > 0) {
      parts.push(`lost ${fmtInt(lost)}`);
    }
    return <span className="font-mono text-xs text-slate-300">{parts.join(" · ")}</span>;
  }

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
      <Stat label="State" value={state} />
      <Stat label="Recv rate" value={fmtRate(rate)} unit="Mb/s" />
      <Stat label="RTT" value={fmtMs(stats["rtt-ms"])} unit="ms" />
      <Stat label="Est. path" value={fmtRate(stats["bandwidth-mbps"])} unit="Mb/s" />
      <Stat label="Latency" value={fmtInt(stats["negotiated-latency-ms"])} unit="ms" />
      <Stat label="Lost (total)" value={fmtInt(lost)} />
    </div>
  );
}
