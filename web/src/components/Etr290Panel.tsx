import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, resetEtr290 } from "../api/client";
import { CompactLiveThumb } from "./LiveThumb";

/** ETSI TR 101 290 P1/P2 counters from TSDuck (via worker stats.etr290). */

type CounterEntry = { value: number; severity: number };

type PidEntry = {
  id: number;
  description: string;
  bitrate_bps?: number;
  share_pct?: number;
  audio?: boolean;
  video?: boolean;
  pmt?: boolean;
  scrambled?: boolean;
  unreferenced?: boolean;
  global?: boolean;
  stuffing?: boolean;
  language?: string;
  service_ids?: number[];
};

type ServiceEntry = {
  id: number;
  name?: string;
  provider?: string;
  type_name?: string;
  pmt_pid?: number;
  pcr_pid?: number;
  bitrate_bps?: number;
  scrambled?: boolean;
  pid_ids?: number[];
};

type PidStructure = {
  tsid?: number;
  ts_bitrate_bps?: number;
  services?: ServiceEntry[];
  pids?: PidEntry[];
};

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
  structure?: PidStructure;
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

function fmtPct(v: unknown): string {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n > 0 && n < 0.1) return "<0.1";
  return n >= 10 ? n.toFixed(1) : n.toFixed(2);
}

function fmtPid(id: number): string {
  return String(id);
}

const MPEG_TS_NULL_PID = 8191;

function pidSharePct(pid: PidEntry, cbr: number | undefined): number | undefined {
  if (typeof pid.share_pct === "number") return pid.share_pct;
  if (cbr && cbr > 0 && typeof pid.bitrate_bps === "number" && pid.bitrate_bps >= 0) {
    return Math.round((10000 * pid.bitrate_bps) / cbr) / 100;
  }
  return undefined;
}

function PidStructureTable({
  structure,
  fallbackCbrBps,
}: {
  structure: PidStructure;
  fallbackCbrBps?: number;
}) {
  const cbr =
    structure.ts_bitrate_bps != null && structure.ts_bitrate_bps > 0
      ? structure.ts_bitrate_bps
      : fallbackCbrBps != null && fallbackCbrBps > 0
        ? fallbackCbrBps
        : undefined;
  const pids = [...(structure.pids ?? [])];
  if (!pids.some((pid) => pid.id === MPEG_TS_NULL_PID)) {
    pids.push({
      id: MPEG_TS_NULL_PID,
      description: "Null padding",
      bitrate_bps: 0,
      stuffing: true,
      global: true,
    });
  }
  pids.sort((a, b) => a.id - b.id);
  const svc = (structure.services ?? [])[0];
  const tsBits: string[] = [];
  if (structure.tsid != null) tsBits.push(`TSID ${structure.tsid}`);
  if (cbr != null) {
    tsBits.push(`CBR ${fmtMbps(cbr)} Mb/s`);
  }
  if (svc) {
    tsBits.push(svc.name?.trim() || `Service ${svc.id}`);
    if (svc.pmt_pid != null) tsBits.push(`PMT ${fmtPid(svc.pmt_pid)}`);
    if (svc.pcr_pid != null) tsBits.push(`PCR ${fmtPid(svc.pcr_pid)}`);
  }

  return (
    <div>
      <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-400">
        PID structure
      </h3>
      {tsBits.length > 0 && (
        <p className="mb-2 text-xs text-slate-500">{tsBits.join(" · ")}</p>
      )}
      <div className="overflow-x-auto rounded-md border border-ink-600">
        <table className="w-full text-left text-xs">
          <thead className="bg-ink-700 text-[10px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-1.5 font-medium">PID</th>
              <th className="px-3 py-1.5 font-medium">Description</th>
              <th className="px-3 py-1.5 font-medium">Lang</th>
              <th className="px-3 py-1.5 text-right font-medium">Mb/s</th>
              <th className="px-3 py-1.5 text-right font-medium">%</th>
            </tr>
          </thead>
          <tbody>
            {pids.map((pid) => {
              const stuffing = pid.stuffing || pid.id === MPEG_TS_NULL_PID;
              const descRaw = pid.description?.trim() ?? "";
              const description =
                stuffing && (!descRaw || descRaw === "Stuffing" || descRaw === "Unknown")
                  ? "Null padding"
                  : pid.description;
              return (
                <tr key={pid.id} className="border-t border-ink-600">
                  <td className="px-3 py-1 font-mono text-slate-300">{fmtPid(pid.id)}</td>
                  <td className="px-3 py-1 text-slate-300">
                    {description}
                    {pid.scrambled ? (
                      <span className="ml-2 text-[10px] uppercase text-signal-warn">scrambled</span>
                    ) : null}
                  </td>
                  <td className="px-3 py-1 font-mono text-slate-500">{pid.language ?? "—"}</td>
                  <td className="px-3 py-1 text-right font-mono text-slate-400">
                    {pid.bitrate_bps != null && pid.bitrate_bps > 0
                      ? fmtMbps(pid.bitrate_bps)
                      : stuffing
                        ? "0.00"
                        : "—"}
                  </td>
                  <td className="px-3 py-1 text-right font-mono text-slate-400">
                    {fmtPct(pidSharePct(pid, cbr))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
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
  thumbnailUrl,
  channelId,
  thumbAlt,
}: {
  receiving: boolean;
  stats: Record<string, unknown> | null | undefined;
  thumbnailUrl?: string | null;
  channelId: string;
  thumbAlt: string;
}) {
  const etr290 = (stats?.etr290 ?? null) as Etr290Stats | null;
  const lastAvailable = useRef<Etr290Stats | null>(null);
  const lastChannelId = useRef(channelId);
  const [resetBusy, setResetBusy] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);

  if (lastChannelId.current !== channelId) {
    lastChannelId.current = channelId;
    lastAvailable.current = null;
  }

  useEffect(() => {
    setResetError(null);
  }, [channelId]);

  if (etr290?.available) {
    lastAvailable.current = etr290;
  }

  const stale = etr290?.reason === "stale";
  const unavailableReason =
    etr290 && !etr290.available && etr290.reason && etr290.reason !== "stale"
      ? etr290.reason
      : null;
  const display =
    etr290?.available ? etr290 : !stale && !unavailableReason ? lastAvailable.current : null;

  const onReset = async () => {
    setResetBusy(true);
    setResetError(null);
    try {
      await resetEtr290(channelId);
    } catch (err) {
      setResetError(err instanceof ApiError ? err.detail : "Reset failed");
    } finally {
      setResetBusy(false);
    }
  };

  let statusBody: ReactNode;
  if (!receiving) {
    statusBody = (
      <p className="text-sm text-slate-500">
        Waiting for transport stream — ETR 290 analysis starts once MPEG-TS is flowing.
      </p>
    );
  } else if (stale && !display) {
    statusBody = (
      <p className="text-sm text-signal-warn">
        ETR 290 snapshot is stale — tsp is not reporting. Counters are not current.
      </p>
    );
  } else if (unavailableReason && !display) {
    statusBody = (
      <p className="text-sm text-slate-500">ETR 290 unavailable ({unavailableReason}).</p>
    );
  } else if (!display) {
    statusBody = <p className="text-sm text-slate-500">Analysing transport stream…</p>;
  } else {
    const worst = display.worst_severity ?? 0;
    const tone = overallTone(worst);
    const interval = display.interval_sec ?? 2;
    const tapDrops = display.tap_drops ?? 0;
    const parts = [`interval ${interval}s`];
    if (display.packet_count != null) {
      parts.push(`${fmtInt(display.packet_count)} packets`);
    }
    if (display.bitrate_bps != null) {
      parts.push(`${fmtMbps(display.bitrate_bps)} Mb/s`);
    }
    if (display.error_count != null) {
      parts.push(`TSDuck errors ${fmtInt(display.error_count)}`);
    }
    statusBody = (
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <span className={`rounded px-2 py-0.5 text-xs font-medium uppercase ${tone.className}`}>
            {tone.label}
          </span>
          <span className="text-xs text-slate-500">{parts.join(" · ")}</span>
          <button
            type="button"
            className="btn-ghost ml-auto text-xs"
            disabled={resetBusy}
            onClick={() => void onReset()}
          >
            {resetBusy ? "Resetting…" : "Reset"}
          </button>
        </div>
        {resetError && <p className="text-xs text-signal-err">{resetError}</p>}
        {tapDrops > 0 && (
          <p className="text-xs text-signal-warn">
            Monitor tap dropped {fmtInt(tapDrops)} queue overruns this session — continuity
            errors may include local drops, not only source defects.
          </p>
        )}
        <p className="text-xs text-slate-500">
          TR 101 290 logical checks (TSDuck). SRT connectivity is separate. PCR repetition
          often alarms on contribution encoders that are not DVB mux compliant. Values are
          session totals since connect or last reset. Packet count and bitrate are the last
          analysis interval.
        </p>
      </div>
    );
  }

  const counters = display?.counters ?? {};

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start gap-4">
        <CompactLiveThumb url={thumbnailUrl} alt={thumbAlt} />
        <div className="min-w-0 flex-1">{statusBody}</div>
      </div>
      {display && (
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
      )}
      {display?.structure && (
        <PidStructureTable
          structure={display.structure}
          fallbackCbrBps={display.bitrate_bps}
        />
      )}
    </div>
  );
}
