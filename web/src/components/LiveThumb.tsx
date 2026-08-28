import { useEffect, useRef, useState } from "react";

/** Polls a live channel JPEG thumbnail with cache-busting.
 *
 * The next frame is decoded off-DOM and swapped in only after load, so a
 * truncated JPEG cannot paint over the previous complete frame.
 */
export function LiveThumb({
  url,
  alt,
  className = "",
  intervalMs = 2000,
}: {
  url: string | null | undefined;
  alt: string;
  className?: string;
  intervalMs?: number;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const hadImage = useRef(false);
  const pending = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    if (!url) {
      setSrc(null);
      setFailed(false);
      hadImage.current = false;
      pending.current = null;
      return;
    }
    let cancelled = false;
    let id: number | undefined;
    const tick = () => {
      if (cancelled) return;
      const next = new Image();
      pending.current = next;
      const cacheBust = `${url}?t=${Date.now()}`;
      next.onload = () => {
        if (cancelled || pending.current !== next) return;
        hadImage.current = true;
        setFailed(false);
        setSrc(cacheBust);
      };
      next.onerror = () => {
        if (cancelled || pending.current !== next) return;
        if (!hadImage.current) setFailed(true);
      };
      next.src = cacheBust;
    };
    const start = () => {
      if (id === undefined) id = window.setInterval(tick, intervalMs);
    };
    const stop = () => {
      if (id !== undefined) {
        window.clearInterval(id);
        id = undefined;
      }
    };
    // One request per interval per live channel is wasted on a hidden tab.
    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        tick();
        start();
      }
    };

    tick();
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      pending.current = null;
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [url, intervalMs]);

  if (!url || !src || (failed && !hadImage.current)) {
    return (
      <div
        className={`flex items-center justify-center bg-ink-900 text-xs text-slate-600 ${className}`}
      >
        No preview
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={alt}
      className={`bg-ink-900 ${className}`}
      onError={() => {
        if (!hadImage.current) setFailed(true);
      }}
    />
  );
}

/** 16:9 frame that does not stretch with the parent width. */
export function CompactLiveThumb({
  url,
  alt,
}: {
  url: string | null | undefined;
  alt: string;
}) {
  return (
    <div className="aspect-video w-64 shrink-0 overflow-hidden rounded-md bg-ink-900">
      <LiveThumb url={url} alt={alt} className="h-full w-full object-contain" />
    </div>
  );
}
