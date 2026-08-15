import { NavLink, Outlet } from "react-router-dom";
import { getHealth } from "../api/client";
import { usePoll } from "../hooks/usePoll";

const NAV = [
  { to: "/", label: "Channels", end: true },
  { to: "/assets", label: "Assets" },
  { to: "/workers", label: "Workers" },
];

function HealthDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-slate-400">
      <span
        className={`h-2 w-2 rounded-full ${ok ? "bg-signal-live" : "bg-signal-err"}`}
        title={`${label}: ${ok ? "ok" : "error"}`}
      />
      {label}
    </span>
  );
}

export function Layout() {
  const { data: health } = usePoll(getHealth, 4000);
  const checks = health?.checks ?? {};

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-ink-600 bg-ink-900/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-8 px-6 py-3">
          <div className="flex items-baseline gap-2">
            <span className="font-semibold tracking-tight text-white">Ingest Farm</span>
            <span className="font-mono text-[10px] uppercase tracking-widest text-slate-500">
              ops
            </span>
          </div>
          <nav className="flex gap-1">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 text-sm ${
                    isActive
                      ? "bg-ink-700 text-white"
                      : "text-slate-400 hover:bg-ink-800 hover:text-slate-200"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-4">
            <HealthDot ok={checks.api === "ok"} label="API" />
            <HealthDot ok={checks.database === "ok"} label="DB" />
            <HealthDot ok={checks.redis === "ok"} label="Redis" />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}
