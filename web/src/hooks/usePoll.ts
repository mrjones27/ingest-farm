import { useEffect, useRef, useState } from "react";

export function usePoll<T>(
  fn: () => Promise<T>,
  intervalMs: number,
  resetKey?: string,
): {
  data: T | null;
  error: string | null;
  reload: () => void;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    setData(null);
  }, [resetKey]);

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      try {
        const next = await fnRef.current();
        if (!cancelled) {
          setData(next);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Request failed");
        }
      }
    };
    void run();
    const timer = window.setInterval(() => void run(), intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [intervalMs, tick, resetKey]);

  return { data, error, reload: () => setTick((n) => n + 1) };
}
