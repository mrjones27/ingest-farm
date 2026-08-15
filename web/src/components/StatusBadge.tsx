const TONE: Record<string, string> = {
  recording: "bg-signal-live/15 text-signal-live ring-signal-live/40",
  starting: "bg-signal-warn/15 text-signal-warn ring-signal-warn/40",
  stopping: "bg-signal-warn/15 text-signal-warn ring-signal-warn/40",
  error: "bg-signal-err/15 text-signal-err ring-signal-err/40",
  failed: "bg-signal-err/15 text-signal-err ring-signal-err/40",
  idle: "bg-white/5 text-slate-400 ring-white/10",
};

export function StatusBadge({ status }: { status: string }) {
  const tone = TONE[status] ?? TONE.idle;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium uppercase tracking-wide ring-1 ${tone}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {status}
    </span>
  );
}
