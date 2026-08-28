"""Tests for TSDuck influx line parsing and ETR 290 rollup."""
from __future__ import annotations

from ingest_farm.worker.etr290 import (
    accumulate_etr290_snapshot,
    empty_etr290_session,
    extract_pid_structure,
    load_pid_structure,
    parse_influx_body,
    parse_influx_line,
    rollup_etr290_snapshot,
    snapshot_is_fresh,
    tsp_command,
    write_json_atomic,
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
    assert "analyze" not in cmd
    assert cmd[cmd.index("--host-url") + 1] == "http://127.0.0.1:8081"


def test_tsp_command_includes_analyze_json(tmp_path) -> None:
    path = tmp_path / "analyze.json"
    cmd = tsp_command(udp_port=9, http_port=8, interval_sec=2, analyze_path=path)
    assert cmd[4:12] == [
        "-P",
        "analyze",
        "--interval",
        "2",
        "--json",
        "--cumulative",
        "-o",
        str(path),
    ]
    assert cmd[cmd.index("-P") + 1 :].count("influx") == 1
    influx_at = cmd.index("influx")
    assert cmd[influx_at - 1] == "-P"


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


def test_accumulate_adds_p12_and_error_count_keeps_interval_rate() -> None:
    first = rollup_etr290_snapshot(parse_influx_body(_SAMPLE_LINES), interval_sec=2, now=1000.0)
    session = accumulate_etr290_snapshot({}, first)
    assert session["counters"]["continuity_count_error"]["value"] == 4
    assert session["error_count"] == 12
    assert session["packet_count"] == 43220
    assert session["bitrate_bps"] == 22936796

    second_body = """
bitrate,scope=ts,tsid=4 value=10000000 1
counter,name=continuity_count_error,severity=1,scope=ts,tsid=4 value=3 1
counter,name=packet_count,severity=4,scope=ts,tsid=4 value=100 1
counter,name=error_count,severity=4,scope=ts,tsid=4 value=3 1
""".strip()
    second = rollup_etr290_snapshot(parse_influx_body(second_body), interval_sec=2, now=1002.0)
    session = accumulate_etr290_snapshot(session, second)
    assert session["counters"]["continuity_count_error"]["value"] == 7
    assert session["counters"]["pcr_repetition_error"]["value"] == 1
    assert session["error_count"] == 15
    assert session["packet_count"] == 100
    assert session["bitrate_bps"] == 10000000
    assert session["worst_severity"] == 1
    assert session["updated_at"] == 1002.0


def test_accumulate_reset_zeros_totals() -> None:
    first = rollup_etr290_snapshot(parse_influx_body(_SAMPLE_LINES), interval_sec=2, now=1.0)
    session = accumulate_etr290_snapshot({}, first)
    reset = empty_etr290_session(interval_sec=2, now=2.0)
    assert reset["counters"]["continuity_count_error"]["value"] == 0
    assert reset["error_count"] == 0
    assert reset["worst_severity"] == 0
    assert "packet_count" not in reset
    again = accumulate_etr290_snapshot(reset, first)
    assert again["counters"]["continuity_count_error"]["value"] == 4


def test_status_payload_holds_last_when_json_unreadable(tmp_path) -> None:
    from ingest_farm.worker.srt_session_proc import _etr290_status_payload

    (tmp_path / "etr290.json").write_text("{not-json", encoding="utf-8")
    last = {
        "available": True,
        "updated_at": 9999999999,
        "interval_sec": 2,
        "worst_severity": 1,
        "counters": {"continuity_count_error": {"value": 4, "severity": 1}},
        "tap_drops": 1,
    }
    out = _etr290_status_payload(tmp_path, live=True, tap_drops=9, last=last)
    assert out is not None
    assert out["available"] is True
    assert out["tap_drops"] == 9
    assert out["counters"]["continuity_count_error"]["value"] == 4


def test_write_json_atomic_roundtrip(tmp_path) -> None:
    path = tmp_path / "snap.json"
    write_json_atomic(path, {"available": True, "n": 3})
    assert path.read_text(encoding="utf-8")
    import json

    assert json.loads(path.read_text(encoding="utf-8")) == {"available": True, "n": 3}


_ANALYZE_JSON = {
    "ts": {"id": 4, "bitrate": 22936796, "pids": {"total": 6, "unreferenced": 0}},
    "services": [
        {
            "id": 1,
            "name": "News",
            "provider": "Ingest",
            "type-name": "digital television",
            "pmt-pid": 256,
            "pcr-pid": 257,
            "bitrate": 8000000,
            "is-scrambled": False,
            "pids": [256, 257, 258],
        }
    ],
    "pids": [
        {
            "id": 0,
            "description": "PAT",
            "bitrate": 15000,
            "global": True,
            "unreferenced": False,
            "pmt": False,
            "audio": False,
            "video": False,
            "is-scrambled": False,
            "services": [],
        },
        {
            "id": 256,
            "description": "PMT",
            "bitrate": 8000,
            "pmt": True,
            "global": False,
            "services": [1],
            "is-scrambled": False,
        },
        {
            "id": 257,
            "description": "AVC video (1920x1080, high profile, level 4.0, 4:2:0)",
            "bitrate": 7000000,
            "video": True,
            "audio": False,
            "services": [1],
            "is-scrambled": False,
        },
        {
            "id": 258,
            "description": "MPEG Audio (eng)",
            "bitrate": 192000,
            "audio": True,
            "language": "eng",
            "services": [1],
            "is-scrambled": False,
        },
        {
            "id": 8191,
            "description": "Stuffing",
            "bitrate": 1573796,
            "global": True,
            "unreferenced": False,
            "is-scrambled": False,
            "services": [],
        },
    ],
}


def test_extract_pid_structure_from_tsduck_json() -> None:
    structure = extract_pid_structure(_ANALYZE_JSON)
    assert structure["tsid"] == 4
    assert structure["ts_bitrate_bps"] == 22936796
    assert structure["services"][0]["name"] == "News"
    assert structure["services"][0]["pmt_pid"] == 256
    assert structure["services"][0]["pcr_pid"] == 257
    assert structure["services"][0]["pid_ids"] == [256, 257, 258]
    by_id = {p["id"]: p for p in structure["pids"]}
    assert by_id[0]["description"] == "PAT"
    assert by_id[0]["global"] is True
    assert by_id[257]["video"] is True
    assert by_id[258]["language"] == "eng"
    assert by_id[258]["bitrate_bps"] == 192000
    assert by_id[258]["share_pct"] == round(100.0 * 192000 / 22936796, 2)
    assert by_id[8191]["description"] == "Null padding"
    assert by_id[8191]["stuffing"] is True
    assert by_id[8191]["share_pct"] == round(100.0 * 1573796 / 22936796, 2)
    assert "tables" not in structure


def test_extract_pid_structure_injects_missing_null_pid() -> None:
    report = {
        "ts": {"id": 1, "bitrate": 10_000_000},
        "services": [],
        "pids": [
            {
                "id": 0,
                "description": "PAT",
                "bitrate": 10_000,
                "global": True,
            }
        ],
    }
    structure = extract_pid_structure(report)
    by_id = {p["id"]: p for p in structure["pids"]}
    assert 8191 in by_id
    assert by_id[8191]["description"] == "Null padding"
    assert by_id[8191]["bitrate_bps"] == 0
    assert by_id[8191]["share_pct"] == 0.0


def test_load_pid_structure_ignores_truncated_json(tmp_path) -> None:
    path = tmp_path / "analyze.json"
    path.write_text("{not-json", encoding="utf-8")
    assert load_pid_structure(path) is None
    write_json_atomic(path, _ANALYZE_JSON)
    loaded = load_pid_structure(path)
    assert loaded is not None
    assert loaded["tsid"] == 4


def test_accumulate_preserves_pid_structure() -> None:
    first = rollup_etr290_snapshot(parse_influx_body(_SAMPLE_LINES), interval_sec=2, now=1.0)
    first["structure"] = extract_pid_structure(_ANALYZE_JSON)
    session = accumulate_etr290_snapshot({}, first)
    second = rollup_etr290_snapshot({"counters": []}, interval_sec=2, now=2.0)
    session = accumulate_etr290_snapshot(session, second)
    assert session["structure"]["pids"][0]["description"] == "PAT"
    assert session["counters"]["continuity_count_error"]["value"] == 4
