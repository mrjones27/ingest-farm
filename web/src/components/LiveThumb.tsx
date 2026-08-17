import { useEffect, useRef, useState } from "react";

/** Polls a live channel JPEG thumbnail with cache-busting. */
export function LiveThumb({
  url,
  alt,
  className = "",
}: {
  url: string | null | undefined;
  alt: string;
  className?: string;
}) {
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const hadImage = useRef(false);

  useEffect(() => {
    if (!url) {
      setSrc(null);
      setFailed(false);
      hadImage.current = false;
      return;
    }
    let cancelled = false;
    let id: number | undefined;
    const tick = () => {
      if (cancelled) return;
      setSrc(`${url}?t=${Date.now()}`);
    };
    const start = () => {
      if (id === undefined) id = window.setInterval(tick, 1000);
    };
    const stop = () => {
      if (id !== undefined) {
        window.clearInterval(id);
        id = undefined;
      }
    };
    // One request per second per live channel is wasted on a hidden tab.
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
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [url]);

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
      className={`object-cover bg-ink-900 ${className}`}
      onLoad={() => {
        hadImage.current = true;
        setFailed(false);
      }}
      onError={() => {
        if (!hadImage.current) setFailed(true);
      }}
    />
  );
}
