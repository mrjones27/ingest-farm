/** ETSI TR 101 290 P1/P2 counters from TSDuck (via worker stats.etr290). */

type CounterEntry = { value: number; severity: number };

type Etr290Stats = {
  available?: boolean;
  interval_sec?: number;
  error_count?: number;
  packet_count?: number;
  bitrate_bps?: number;
  worst_severity?: number;
  tap_drops?: number;
  reason?: string;
  counters?: Record<string, CounterEntry>;
};

const P1_ORDER = [
  "ts_sync_loss",
  "sync_byte_error",
  "pat_error",
  "pat_error_2",
  "continuity_count_error",
  "pmt_error",
  "pmt_error_2",
  "pid_error",
] as const;

const P2_PCR_ORDER = [
  "pcr_error",
  "pcr_repetition_error",
  "pcr_discontinuity_indicator_error",
] as const;

const P2_OTHER_ORDER = [
  "transport_error",
  "crc_error",
  "crc_error_2",
  "pts_error",
  "cat_error",
] as const;

const LABELS: Record<string, string> = {
  ts_sync_loss: "TS sync loss",
  sync_byte_error: "Sync byte",
  pat_error: "PAT",
  pat_error_2: "PAT (detail)",
  continuity_count_error: "Continuity count",
  pmt_error: "PMT",
  pmt_error_2: "PMT (detail)",
  pid_error: "Missing PID",
  transport_error: "Transport error flag",
  crc_error: "CRC (PSI)",
  crc_error_2: "CRC (other)",
  pcr_error: "PCR discontinuity",
  pcr_repetition_error: "PCR repetition (>100 ms)",
  pcr_discontinuity_indicator_error: "PCR without indicator",
  pts_error: "PTS repetition (>700 ms)",
  cat_error: "CAT",
};

function fmtInt(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString();
}

function fmtMbps(bps: unknown): string {
  const n = typeof bps === "number" ? bps : Number(bps);
  if (!Number.isFinite(n) || n <= 0) return "—";
  const mbps = n / 1_000_000;
  return mbps >= 10 ? mbps.toFixed(1) : mbps.toFixed(2);
}

function overallTone(worst: number): { label: string; className: string } {
  if (worst === 1) return { label: "P1 errors", className: "bg-signal-err/20 text-signal-err" };
  if (worst === 2) return { label: "P2 warnings", className: "bg-signal-warn/20 text-signal-warn" };
  return { label: "OK", className: "bg-signal-ok/20 text-signal-ok" };
}

function CounterRow({ name, entry }: { name: string; entry: CounterEntry | undefined }) {
  const value = entry?.value ?? 0;
  const hot = value > 0;
  return (
    <div className="flex items-center justify-between gap-2 py-1">
      <span className="text-xs text-slate-400">{LABELS[name] ?? name}</span>
      <span
        className={
          hot
            ? "font-mono text-sm text-signal-err"
            : "font-mono text-sm text-slate-500"
        }
      >
        {fmtInt(value)}
      </span>
    </div>
  );
}

export function Etr290Panel({
  receiving,
  stats,
}: {
  receiving: boolean;
  stats: Record<string, unknown> | null | undefined;
}) {
  const etr290 = (stats?.etr290 ?? null) as Etr290Stats | null;

  if (!receiving) {
    return (
      <p className="text-sm text-slate-500">
        Waiting for transport stream — ETR 290 analysis starts once MPEG-TS is flowing.
      </p>
    );
  }

  if (!etr290?.available) {
    const reason = etr290?.reason;
    if (reason === "stale") {
      return (
        <p className="text-sm text-signal-warn">
          ETR 290 snapshot is stale — tsp is not reporting. Counters are not current.
        </p>
      );
    }
    return (
      <p className="text-sm text-slate-500">
        {reason ? `ETR 290 unavailable (${reason}).` : "Analysing transport stream…"}
      </p>
    );
  }

  const counters = etr290.counters ?? {};
  const worst = etr290.worst_severity ?? 0;
  const tone = overallTone(worst);
  const interval = etr290.interval_sec ?? 2;
  const tapDrops = etr290.tap_drops ?? 0;
  const parts = [`interval ${interval}s`];
  if (etr290.packet_count != null) {
    parts.push(`${fmtInt(etr290.packet_count)} packets`);
  }
  if (etr290.bitrate_bps != null) {
    parts.push(`${fmtMbps(etr290.bitrate_bps)} Mb/s`);
  }
  if (etr290.error_count != null) {
    parts.push(`TSDuck errors ${fmtInt(etr290.error_count)}`);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className={`rounded px-2 py-0.5 text-xs font-medium uppercase ${tone.className}`}>
          {tone.label}
        </span>
        <span className="text-xs text-slate-500">{parts.join(" · ")}</span>
      </div>
      {tapDrops > 0 && (
        <p className="text-xs text-signal-warn">
          Monitor tap dropped {fmtInt(tapDrops)} queue overruns this session — continuity
          errors may include local drops, not only source defects.
        </p>
      )}
      <p className="text-xs text-slate-500">
        TR 101 290 logical checks (TSDuck). SRT connectivity is separate. PCR repetition
        often alarms on contribution encoders that are not DVB mux compliant. Values are
        errors in the last analysis interval, not session totals.
      </p>
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            Priority 1
          </h3>
          <div className="divide-y divide-ink-600 rounded-md border border-ink-600 px-3">
            {P1_ORDER.map((name) => (
              <CounterRow key={name} name={name} entry={counters[name]} />
            ))}
          </div>
        </div>
        <div>
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            Priority 2
          </h3>
          <div className="mb-3 rounded-md border border-ink-600 px-3">
            <div className="py-1 text-[10px] uppercase tracking-wide text-slate-500">PCR</div>
            {P2_PCR_ORDER.map((name) => (
              <CounterRow key={name} name={name} entry={counters[name]} />
            ))}
          </div>
          <div className="divide-y divide-ink-600 rounded-md border border-ink-600 px-3">
            {P2_OTHER_ORDER.map((name) => (
              <CounterRow key={name} name={name} entry={counters[name]} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
