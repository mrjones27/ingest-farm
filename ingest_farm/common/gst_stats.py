from __future__ import annotations

from typing import Any


def gst_structure_to_dict(structure: Any) -> dict[str, Any]:
    """Convert a GstStructure (e.g. SRT stats) into JSON-serializable data."""
    if structure is None:
        return {}

    out: dict[str, Any] = {}
    try:
        n = structure.n_fields()
    except Exception:
        return out

    for i in range(n):
        name = structure.nth_field_name(i)
        try:
            value = structure.get_value(name)
        except Exception:
            continue
        out[name] = _gst_value_to_python(value)
    return out


def _gst_value_to_python(value: Any) -> Any:
    type_name = type(value).__name__
    if type_name == "Structure" or hasattr(value, "n_fields") and hasattr(value, "get_value"):
        try:
            return gst_structure_to_dict(value)
        except Exception:
            return str(value)

    # GValueArray / list of nested structures (listener callers)
    if hasattr(value, "n_values") and hasattr(value, "get_nth"):
        items = []
        for i in range(value.n_values):
            try:
                items.append(_gst_value_to_python(value.get_nth(i)))
            except Exception:
                continue
        return items

    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")

    if isinstance(value, (bool, int, float, str)):
        return value

    # SocketAddress etc. — stringify
    try:
        return str(value)
    except Exception:
        return None


def normalize_srt_stats(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten listener caller stats and keep a stable UI-friendly shape."""
    if not raw:
        return {}

    callers = raw.get("callers")
    primary = raw
    if isinstance(callers, list) and callers:
        first = callers[0]
        if isinstance(first, dict):
            primary = {**first, **{k: v for k, v in raw.items() if k != "callers"}}

    keys = (
        "rtt-ms",
        "bandwidth-mbps",
        "receive-rate-mbps",
        "send-rate-mbps",
        "negotiated-latency-ms",
        "packets-received",
        "packets-received-lost",
        "packets-sent",
        "packets-sent-lost",
        "packets-retransmitted",
        "bytes-received",
        "bytes-received-total",
        "bytes-sent",
        "bytes-sent-total",
        "bytes-received-lost",
        "packet-nack-sent",
        "packet-nack-received",
    )
    out: dict[str, Any] = {"available": True}
    for key in keys:
        if key in primary and primary[key] is not None:
            out[key] = primary[key]
    if isinstance(callers, list):
        out["caller_count"] = len(callers)
    return out
