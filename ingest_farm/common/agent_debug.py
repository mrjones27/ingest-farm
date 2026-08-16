"""Debug-mode NDJSON logger (session 9c0795). Safe no-op on failure."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

_SESSION = "9c0795"
_PATHS = (
    Path("/data/debug-9c0795.log"),
    Path("debug-9c0795.log"),
    Path("/app/debug-9c0795.log"),
)
_URLS = (
    "http://host.docker.internal:7830/ingest/17c42ba2-af71-42fd-af39-3ec8169963cf",
    "http://127.0.0.1:7830/ingest/17c42ba2-af71-42fd-af39-3ec8169963cf",
)


def agent_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict[str, Any] | None = None,
    *,
    run_id: str = "pre-fix",
) -> None:
    payload = {
        "sessionId": _SESSION,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    line = json.dumps(payload, default=str) + "\n"
    for path in _PATHS:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            break
        except Exception:
            continue
    body = line.encode("utf-8")
    for url in _URLS:
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Debug-Session-Id": _SESSION,
                },
                method="POST",
            )
            urllib.request.urlopen(req, timeout=0.5)
            break
        except Exception:
            continue
