"""Tests for TSDuck influx line parsing and ETR 290 rollup."""
from __future__ import annotations

from ingest_farm.worker.etr290 import (
    parse_influx_body,
    parse_influx_line,
    rollup_etr290_snapshot,
    snapshot_is_fresh,
    tsp_command,
)

_SAMPLE_LINES = """
bitrate,scope=ts,tsid=4 value=22936796 1548161530912
counter,name=continuity_count_error,severity=1,scope=ts,tsid=4 value=4 1548161530912
counter,name=pcr_repetition_error,severity=2,scope=ts,tsid=4 value=1 1548161530912
counter,name=continuity_count_error,severity=1,scope=service,tsid=4,service=1025 value=44 1548161530912
counter,name=pat_error,severity=1,scope=ts,tsid=4 value=0 1548161530912
counter,name=packet_count,severity=4,scope=ts,tsid=4 value=43220 1548161530912
counter,name=error_count,severity=4,scope=ts,tsid=4 value=12 1548161530912
counter,name=nit_error,severity=3,scope=ts,tsid=4 value=9 1548161530912
""".strip()


def test_parse_influx_line_ignores_non_ts_scope() -> None:
    line = "counter,name=continuity_count_error,severity=1,scope=service,tsid=4 value=44 1"
    assert parse_influx_line(line) is None


def test_parse_influx_line_counter() -> None:
    line = "counter,name=continuity_count_error,severity=1,scope=ts,tsid=4 value=4 1548161530912"
    parsed = parse_influx_line(line)
    assert parsed == {
        "kind": "counter",
        "name": "continuity_count_error",
        "severity": 1,
        "value": 4,
    }


def test_parse_influx_line_without_timestamp() -> None:
    line = "counter,name=pat_error,severity=1,scope=ts,tsid=4 value=2"
    parsed = parse_influx_line(line)
    assert parsed == {"kind": "counter", "name": "pat_error", "severity": 1, "value": 2}


def test_parse_influx_line_unescapes_tag_commas() -> None:
    line = r"counter,name=pat_error,severity=1,scope=ts,tsid=4 value=1 1"
    assert parse_influx_line(line) is not None


def test_parse_influx_body_filters_scope_and_p3() -> None:
    parsed = parse_influx_body(_SAMPLE_LINES)
    names = {r["name"] for r in parsed["counters"]}
    assert names == {"continuity_count_error", "pcr_repetition_error", "pat_error"}
    assert parsed["bitrate_bps"] == 22936796
    assert parsed["packet_count"] == 43220
    assert parsed["error_count"] == 12


def test_rollup_uses_tsduck_error_count_not_sum() -> None:
    parsed = parse_influx_body(_SAMPLE_LINES)
    snap = rollup_etr290_snapshot(parsed, interval_sec=2, now=1000.0)
    assert snap["available"] is True
    assert snap["worst_severity"] == 1
    assert snap["error_count"] == 12
    assert snap["packet_count"] == 43220
    assert snap["bitrate_bps"] == 22936796
    assert snap["updated_at"] == 1000.0
    assert snap["counters"]["continuity_count_error"]["value"] == 4
    assert snap["counters"]["pcr_repetition_error"]["value"] == 1
    assert snap["counters"]["pat_error"]["value"] == 0


def test_rollup_all_p12_counters_present() -> None:
    snap = rollup_etr290_snapshot({"counters": []}, interval_sec=2, now=1.0)
    assert "ts_sync_loss" in snap["counters"]
    assert "pcr_discontinuity_indicator_error" in snap["counters"]
    assert snap["worst_severity"] == 0
    assert "error_count" not in snap


def test_snapshot_is_fresh_within_window() -> None:
    snap = {"available": True, "updated_at": 100.0, "interval_sec": 2}
    assert snapshot_is_fresh(snap, now=109.0) is True
    assert snapshot_is_fresh(snap, now=111.0) is False


def test_snapshot_unavailable_is_not_fresh() -> None:
    snap = {"available": False, "updated_at": 100.0, "interval_sec": 2, "reason": "tsp exited 1"}
    assert snapshot_is_fresh(snap, now=101.0) is False


def test_tsp_command_uses_positional_ip_port() -> None:
    cmd = tsp_command(udp_port=54321, http_port=8081, interval_sec=2)
    assert cmd[:4] == ["tsp", "-I", "ip", "54321"]
    assert "-r" not in cmd
    assert "--tr-101-290" in cmd
    assert "--bitrate" in cmd
    assert cmd[cmd.index("--host-url") + 1] == "http://127.0.0.1:8081"


def test_status_payload_marks_stale_snapshot(tmp_path) -> None:
    from ingest_farm.worker.srt_session_proc import _etr290_status_payload

    (tmp_path / "etr290.json").write_text(
        '{"available": true, "updated_at": 1, "interval_sec": 2, "worst_severity": 0}',
        encoding="utf-8",
    )
    out = _etr290_status_payload(tmp_path, live=True, tap_drops=3)
    assert out == {"available": False, "reason": "stale", "tap_drops": 3}


def test_status_payload_omits_when_not_receiving(tmp_path) -> None:
    from ingest_farm.worker.srt_session_proc import _etr290_status_payload

    (tmp_path / "etr290.json").write_text(
        '{"available": true, "updated_at": 9999999999, "interval_sec": 2}',
        encoding="utf-8",
    )
    assert _etr290_status_payload(tmp_path, live=False, tap_drops=0) is None


def test_status_payload_passes_tsp_failure_reason(tmp_path) -> None:
    from ingest_farm.worker.srt_session_proc import _etr290_status_payload

    (tmp_path / "etr290.json").write_text(
        '{"available": false, "reason": "tsp exited 1: unknown option -r", "updated_at": 1}',
        encoding="utf-8",
    )
    out = _etr290_status_payload(tmp_path, live=True, tap_drops=0)
    assert out is not None
    assert out["available"] is False
    assert "unknown option" in out["reason"]
