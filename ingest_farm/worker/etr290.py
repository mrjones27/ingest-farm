"""ETSI TR 101 290 (ETR 290) P1/P2 counter parsing from TSDuck influx line protocol."""
from __future__ import annotations

import json
import time
from pathlib import Path
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

# MPEG-TS null / stuffing PID (ISO/IEC 13818-1). TSDuck labels it "Stuffing".
MPEG_TS_NULL_PID = 8191

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


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write JSON via a same-directory replace so readers never see a partial file."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        try:
            path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def empty_etr290_session(*, interval_sec: int, now: float | None = None) -> dict[str, Any]:
    """Zeroed P1/P2 session totals (bitrate/packets omitted until the first interval)."""
    counters: dict[str, dict[str, int]] = {}
    for name in ALL_P12_COUNTERS:
        sev = 1 if name in P1_COUNTERS else 2
        counters[name] = {"value": 0, "severity": sev}
    return {
        "available": True,
        "interval_sec": interval_sec,
        "updated_at": time.time() if now is None else now,
        "worst_severity": 0,
        "counters": counters,
        "error_count": 0,
    }


def accumulate_etr290_snapshot(
    session: dict[str, Any],
    interval_snap: dict[str, Any],
) -> dict[str, Any]:
    """Add interval P1/P2 counters and error_count into session totals.

    ``bitrate_bps`` and ``packet_count`` stay last-interval (rate/window metrics).
    ``worst_severity`` is recomputed from the running totals.
    """
    interval_sec = int(interval_snap.get("interval_sec") or session.get("interval_sec") or 2)
    if not session.get("counters"):
        base = empty_etr290_session(interval_sec=interval_sec, now=0.0)
    else:
        base = {
            "available": True,
            "interval_sec": interval_sec,
            "updated_at": session.get("updated_at"),
            "counters": {k: dict(v) for k, v in session["counters"].items()},
            "error_count": int(session.get("error_count") or 0),
        }

    counters: dict[str, dict[str, int]] = dict(base["counters"])
    for name, entry in (interval_snap.get("counters") or {}).items():
        prev = counters.get(name, {"value": 0, "severity": int(entry.get("severity", 2))})
        counters[name] = {
            "value": int(prev.get("value", 0)) + int(entry.get("value", 0)),
            "severity": int(entry.get("severity", prev.get("severity", 2))),
        }
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

    out: dict[str, Any] = {
        "available": True,
        "interval_sec": interval_sec,
        "updated_at": interval_snap.get("updated_at", time.time()),
        "worst_severity": worst,
        "counters": counters,
        "error_count": int(base.get("error_count") or 0) + int(interval_snap.get("error_count") or 0),
    }
    if interval_snap.get("packet_count") is not None:
        out["packet_count"] = int(interval_snap["packet_count"])
    elif session.get("packet_count") is not None:
        out["packet_count"] = int(session["packet_count"])
    if interval_snap.get("bitrate_bps") is not None:
        out["bitrate_bps"] = int(interval_snap["bitrate_bps"])
    elif session.get("bitrate_bps") is not None:
        out["bitrate_bps"] = int(session["bitrate_bps"])
    structure = interval_snap.get("structure") or session.get("structure")
    if isinstance(structure, dict):
        out["structure"] = structure
    return out


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


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _as_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def extract_pid_structure(report: dict[str, Any]) -> dict[str, Any]:
    """Slim TSDuck ``analyze --json`` into the Redis/API ``etr290.structure`` shape.

    Field names match TSDuck 3.44 ``TSAnalyzerReport::reportJSON`` (kebab-case
    in the file, snake_case in our snapshot). Unknown keys are ignored.
    """
    ts_node = report.get("ts") if isinstance(report.get("ts"), dict) else {}
    tsid = _as_int(ts_node.get("id")) if isinstance(ts_node, dict) else None
    ts_bitrate = _as_int(ts_node.get("bitrate")) if isinstance(ts_node, dict) else None

    services_out: list[dict[str, Any]] = []
    for raw in report.get("services") or []:
        if not isinstance(raw, dict):
            continue
        sid = _as_int(raw.get("id"))
        if sid is None:
            continue
        entry: dict[str, Any] = {"id": sid}
        name = _as_str(raw.get("name"))
        if name:
            entry["name"] = name
        provider = _as_str(raw.get("provider"))
        if provider:
            entry["provider"] = provider
        type_name = _as_str(raw.get("type-name"))
        if type_name:
            entry["type_name"] = type_name
        pmt_pid = _as_int(raw.get("pmt-pid"))
        if pmt_pid is not None:
            entry["pmt_pid"] = pmt_pid
        pcr_pid = _as_int(raw.get("pcr-pid"))
        if pcr_pid is not None:
            entry["pcr_pid"] = pcr_pid
        bitrate = _as_int(raw.get("bitrate"))
        if bitrate is not None:
            entry["bitrate_bps"] = bitrate
        if isinstance(raw.get("is-scrambled"), bool):
            entry["scrambled"] = raw["is-scrambled"]
        pid_ids = [n for n in (_as_int(p) for p in (raw.get("pids") or [])) if n is not None]
        entry["pid_ids"] = pid_ids
        services_out.append(entry)
    services_out.sort(key=lambda s: s["id"])

    pids_out: list[dict[str, Any]] = []
    for raw in report.get("pids") or []:
        if not isinstance(raw, dict):
            continue
        pid = _as_int(raw.get("id"))
        if pid is None:
            continue
        entry = {
            "id": pid,
            "description": _as_str(raw.get("description")) or "Unknown",
        }
        bitrate = _as_int(raw.get("bitrate"))
        if bitrate is not None:
            entry["bitrate_bps"] = bitrate
        for src, dest in (
            ("audio", "audio"),
            ("video", "video"),
            ("pmt", "pmt"),
            ("unreferenced", "unreferenced"),
            ("global", "global"),
        ):
            if isinstance(raw.get(src), bool):
                entry[dest] = raw[src]
        if isinstance(raw.get("is-scrambled"), bool):
            entry["scrambled"] = raw["is-scrambled"]
        lang = _as_str(raw.get("language"))
        if lang:
            entry["language"] = lang
        svc_ids = [n for n in (_as_int(s) for s in (raw.get("services") or [])) if n is not None]
        if svc_ids:
            entry["service_ids"] = svc_ids
        pids_out.append(entry)

    if not any(p["id"] == MPEG_TS_NULL_PID for p in pids_out):
        pids_out.append(
            {
                "id": MPEG_TS_NULL_PID,
                "description": "Null padding",
                "bitrate_bps": 0,
                "stuffing": True,
                "global": True,
            }
        )
    else:
        for pid in pids_out:
            if pid["id"] != MPEG_TS_NULL_PID:
                continue
            pid["stuffing"] = True
            desc = str(pid.get("description") or "").strip().lower()
            if desc in {"", "stuffing", "unknown"}:
                pid["description"] = "Null padding"

    if ts_bitrate is not None and ts_bitrate > 0:
        for pid in pids_out:
            bps = pid.get("bitrate_bps")
            if isinstance(bps, int) and bps >= 0:
                pid["share_pct"] = round(100.0 * bps / ts_bitrate, 2)

    pids_out.sort(key=lambda p: p["id"])

    out: dict[str, Any] = {"services": services_out, "pids": pids_out}
    if tsid is not None:
        out["tsid"] = tsid
    if ts_bitrate is not None:
        out["ts_bitrate_bps"] = ts_bitrate
    return out


def load_pid_structure(path: Path) -> dict[str, Any] | None:
    """Read a TSDuck analyze JSON file, or None if missing/unreadable."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("pids"), list):
        return None
    return extract_pid_structure(data)


def tsp_command(
    *,
    udp_port: int,
    http_port: int,
    interval_sec: int,
    analyze_path: Path | None = None,
) -> list[str]:
    """Build the documented ``tsp -I ip <port>`` command for TR 101 290 P1/P2.

    ``--max-severity 4`` is required so TSDuck also emits ``packet_count`` and
    ``error_count``. Priority 3 SI counters are still dropped by the parser.

    When ``analyze_path`` is set, ``-P analyze --json --cumulative`` rewrites
    that file each interval (TSDuck 3.44 analyze plugin).
    """
    interval = str(max(1, int(interval_sec)))
    cmd: list[str] = ["tsp", "-I", "ip", str(int(udp_port))]
    if analyze_path is not None:
        cmd.extend(
            [
                "-P",
                "analyze",
                "--interval",
                interval,
                "--json",
                "--cumulative",
                "-o",
                str(analyze_path),
            ]
        )
    cmd.extend(
        [
            "-P",
            "influx",
            "--tr-101-290",
            "--bitrate",
            "--max-severity",
            "4",
            "--interval",
            interval,
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
    )
    return cmd
