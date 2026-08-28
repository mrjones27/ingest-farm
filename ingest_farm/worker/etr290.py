"""ETSI TR 101 290 (ETR 290) P1/P2 counter parsing from TSDuck influx line protocol."""
from __future__ import annotations

import time
from typing import Any

# Priority 1 — transport stream sync / PSI / continuity (ETSI TR 101 290 §5.2.1).
P1_COUNTERS: tuple[str, ...] = (
    "ts_sync_loss",
    "sync_byte_error",
    "pat_error",
    "pat_error_2",
    "continuity_count_error",
    "pmt_error",
    "pmt_error_2",
    "pid_error",
)

# Priority 2 — CRC / PCR / PTS / CAT (ETSI TR 101 290 §5.2.2). PCR group first in UI.
P2_PCR_COUNTERS: tuple[str, ...] = (
    "pcr_error",
    "pcr_repetition_error",
    "pcr_discontinuity_indicator_error",
)

P2_OTHER_COUNTERS: tuple[str, ...] = (
    "transport_error",
    "crc_error",
    "crc_error_2",
    "pts_error",
    "cat_error",
)

P2_COUNTERS: tuple[str, ...] = P2_PCR_COUNTERS + P2_OTHER_COUNTERS

ALL_P12_COUNTERS: frozenset[str] = frozenset(P1_COUNTERS + P2_COUNTERS)

# TSDuck informational (severity 4). Used as denominator / official error total.
_INFO_COUNTERS: frozenset[str] = frozenset({"packet_count", "error_count"})

# Treat a snapshot older than this many report intervals as stale.
STALE_INTERVALS = 3
MIN_FRESH_SEC = 10.0


def _unescape_influx_tag(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            out.append(value[i + 1])
            i += 2
            continue
        out.append(value[i])
        i += 1
    return "".join(out)


def _split_measurement_and_fields(line: str) -> tuple[str, str] | None:
    """Split ``measurement,tags fields [timestamp]``. Timestamp is optional."""
    try:
        head, rest = line.split(" ", 1)
    except ValueError:
        return None
    rest = rest.strip()
    if not rest:
        return None
    # Trailing integer timestamp is optional in line protocol.
    parts = rest.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].lstrip("-").isdigit():
        fields_part = parts[0]
    else:
        fields_part = rest
    return head, fields_part


def _field_numeric(fields_part: str, name: str = "value") -> float | None:
    for field in fields_part.split(","):
        if "=" not in field:
            continue
        key, raw = field.split("=", 1)
        if key != name:
            continue
        if raw.endswith("i"):
            raw = raw[:-1]
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def parse_influx_line(line: str) -> dict[str, Any] | None:
    """Parse one Influx line-protocol row from TSDuck ``influx --tr-101-290``."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    split = _split_measurement_and_fields(line)
    if split is None:
        return None
    head, fields_part = split

    measurement, _, tag_blob = head.partition(",")
    tags: dict[str, str] = {}
    if tag_blob:
        for part in tag_blob.split(","):
            if "=" not in part:
                continue
            key, val = part.split("=", 1)
            tags[key] = _unescape_influx_tag(val)

    if tags.get("scope") != "ts":
        return None

    numeric = _field_numeric(fields_part)
    if numeric is None:
        return None

    if measurement == "bitrate":
        return {"kind": "bitrate", "value": int(numeric)}

    if measurement != "counter":
        return None

    try:
        severity = int(tags.get("severity", "0"))
    except ValueError:
        return None

    name = tags.get("name")
    if not name:
        return None
    if severity in (1, 2) and name in ALL_P12_COUNTERS:
        return {"kind": "counter", "name": name, "severity": severity, "value": int(numeric)}
    if severity == 4 and name in _INFO_COUNTERS:
        return {"kind": "info", "name": name, "severity": 4, "value": int(numeric)}
    return None


def parse_influx_body(body: str) -> dict[str, Any]:
    """Parse a TSDuck influx write body into counters plus optional info fields."""
    counters: list[dict[str, Any]] = []
    bitrate_bps: int | None = None
    packet_count: int | None = None
    error_count: int | None = None
    for line in body.splitlines():
        parsed = parse_influx_line(line)
        if parsed is None:
            continue
        kind = parsed.get("kind")
        if kind == "counter":
            counters.append(parsed)
        elif kind == "bitrate":
            bitrate_bps = int(parsed["value"])
        elif kind == "info" and parsed["name"] == "packet_count":
            packet_count = int(parsed["value"])
        elif kind == "info" and parsed["name"] == "error_count":
            error_count = int(parsed["value"])
    return {
        "counters": counters,
        "bitrate_bps": bitrate_bps,
        "packet_count": packet_count,
        "error_count": error_count,
    }


def rollup_etr290_snapshot(
    parsed: dict[str, Any] | list[dict[str, Any]],
    *,
    interval_sec: int,
    now: float | None = None,
) -> dict[str, Any]:
    """Merge parsed TSDuck rows into the Redis/API ``etr290`` snapshot shape.

    ``error_count`` is TSDuck's own interval total when present. It is not a
    sum of P1/P2 counters (those overlap by spec).
    """
    if isinstance(parsed, list):
        parsed = {
            "counters": parsed,
            "bitrate_bps": None,
            "packet_count": None,
            "error_count": None,
        }

    counters: dict[str, dict[str, int]] = {}
    for row in parsed.get("counters") or []:
        name = row["name"]
        counters[name] = {"value": int(row["value"]), "severity": int(row["severity"])}

    for name in ALL_P12_COUNTERS:
        if name not in counters:
            sev = 1 if name in P1_COUNTERS else 2
            counters[name] = {"value": 0, "severity": sev}

    worst = 0
    for entry in counters.values():
        if entry["value"] <= 0:
            continue
        if entry["severity"] == 1:
            worst = 1
        elif entry["severity"] == 2 and worst != 1:
            worst = 2

    snap: dict[str, Any] = {
        "available": True,
        "interval_sec": interval_sec,
        "updated_at": time.time() if now is None else now,
        "worst_severity": worst,
        "counters": counters,
    }
    if parsed.get("error_count") is not None:
        snap["error_count"] = int(parsed["error_count"])
    if parsed.get("packet_count") is not None:
        snap["packet_count"] = int(parsed["packet_count"])
    if parsed.get("bitrate_bps") is not None:
        snap["bitrate_bps"] = int(parsed["bitrate_bps"])
    return snap


def snapshot_is_fresh(snap: dict[str, Any], *, now: float | None = None) -> bool:
    """True when ``updated_at`` is within a few TSDuck report intervals."""
    if not snap.get("available"):
        return False
    updated = snap.get("updated_at")
    if not isinstance(updated, (int, float)):
        return False
    interval = max(1, int(snap.get("interval_sec") or 2))
    age = (time.time() if now is None else now) - float(updated)
    return age <= max(MIN_FRESH_SEC, STALE_INTERVALS * interval)


def tsp_command(*, udp_port: int, http_port: int, interval_sec: int) -> list[str]:
    """Build the documented ``tsp -I ip <port>`` command for TR 101 290 P1/P2.

    ``--max-severity 4`` is required so TSDuck also emits ``packet_count`` and
    ``error_count``. Priority 3 SI counters are still dropped by the parser.
    """
    return [
        "tsp",
        "-I",
        "ip",
        str(int(udp_port)),
        "-P",
        "influx",
        "--tr-101-290",
        "--bitrate",
        "--max-severity",
        "4",
        "--interval",
        str(max(1, int(interval_sec))),
        "--host-url",
        f"http://127.0.0.1:{int(http_port)}",
        "--bucket",
        "local",
        "--org",
        "ingest-farm",
        "--token",
        "local",
        "-O",
        "drop",
    ]
