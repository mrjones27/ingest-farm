"""Sidecar: tsp influx --tr-101-290 → local Influx write stub → etr290.json."""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ingest_farm.worker.etr290 import (
    accumulate_etr290_snapshot,
    empty_etr290_session,
    load_pid_structure,
    parse_influx_body,
    rollup_etr290_snapshot,
    tsp_command,
    write_json_atomic,
)

_MAX_WRITE_BYTES = 1_048_576
_STDERR_KEEP = 50


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _InfluxStubHandler(BaseHTTPRequestHandler):
    snapshot_lock: threading.Lock
    snapshot: dict[str, Any]
    interval_sec: int

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/api/v2/write":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        if length < 0 or length > _MAX_WRITE_BYTES:
            self.send_response(413)
            self.end_headers()
            return
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        parsed = parse_influx_body(body)
        if parsed["counters"] or parsed["packet_count"] is not None:
            interval_snap = rollup_etr290_snapshot(parsed, interval_sec=self.interval_sec)
            with self.snapshot_lock:
                accumulated = accumulate_etr290_snapshot(dict(self.snapshot), interval_snap)
                self.snapshot.clear()
                self.snapshot.update(accumulated)
        self.send_response(204)
        self.end_headers()


def _drain_stderr(stream: Any, buf: list[str]) -> None:
    try:
        for line in iter(stream.readline, ""):
            buf.append(line.rstrip())
            if len(buf) > _STDERR_KEEP:
                del buf[: len(buf) - _STDERR_KEEP]
    except (OSError, ValueError):
        return


def _tsp_failure_reason(returncode: int | None, stderr_buf: list[str]) -> str:
    tail = " ".join(line.strip() for line in stderr_buf[-8:] if line.strip())
    if tail:
        return f"tsp exited {returncode}: {tail[:400]}"
    return f"tsp exited {returncode}"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 2:
        print(
            "usage: python -m ingest_farm.worker.etr290_sidecar <state_dir> <udp_port>",
            file=sys.stderr,
        )
        return 2

    state_dir = Path(args[0])
    try:
        udp_port = int(args[1])
    except ValueError:
        print("udp_port must be an integer", file=sys.stderr)
        return 2
    state_dir.mkdir(parents=True, exist_ok=True)
    ports_path = state_dir / "etr290_ports.json"
    snapshot_path = state_dir / "etr290.json"
    analyze_path = state_dir / "analyze.json"
    stop_path = state_dir / "etr290_stop"
    reset_path = state_dir / "etr290_reset"

    if shutil.which("tsp") is None:
        write_json_atomic(
            snapshot_path,
            {"available": False, "reason": "tsp not installed", "updated_at": time.time()},
        )
        write_json_atomic(ports_path, {"udp_port": udp_port, "http_port": 0, "ready": False})
        return 1

    from ingest_farm.config import get_settings

    settings = get_settings()
    interval = max(1, int(settings.etr290_interval_sec))
    http_port = _free_tcp_port()

    snapshot: dict[str, Any] = {"available": False}
    snap_lock = threading.Lock()

    handler_cls = type(
        "_Handler",
        (_InfluxStubHandler,),
        {
            "snapshot_lock": snap_lock,
            "snapshot": snapshot,
            "interval_sec": interval,
        },
    )
    httpd = ThreadingHTTPServer(("127.0.0.1", http_port), handler_cls)
    http_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    http_thread.start()

    write_json_atomic(
        ports_path,
        {"udp_port": udp_port, "http_port": http_port, "ready": True, "interval_sec": interval},
    )

    tsp_cmd = tsp_command(
        udp_port=udp_port,
        http_port=http_port,
        interval_sec=interval,
        analyze_path=analyze_path,
    )
    proc = subprocess.Popen(
        tsp_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    stderr_buf: list[str] = []
    if proc.stderr is not None:
        threading.Thread(target=_drain_stderr, args=(proc.stderr, stderr_buf), daemon=True).start()

    exit_code = 0
    try:
        while True:
            if stop_path.exists():
                break
            rc = proc.poll()
            if rc is not None:
                reason = _tsp_failure_reason(rc, stderr_buf)
                write_json_atomic(
                    snapshot_path,
                    {"available": False, "reason": reason, "updated_at": time.time()},
                )
                print(reason, flush=True)
                exit_code = 1
                break
            if reset_path.exists():
                with snap_lock:
                    structure = snapshot.get("structure")
                    snapshot.clear()
                    snapshot.update(empty_etr290_session(interval_sec=interval))
                    if isinstance(structure, dict):
                        snapshot["structure"] = structure
                    write_json_atomic(snapshot_path, dict(snapshot))
                reset_path.unlink(missing_ok=True)
            structure = load_pid_structure(analyze_path)
            with snap_lock:
                if structure is not None:
                    snapshot["structure"] = structure
                if snapshot.get("available"):
                    write_json_atomic(snapshot_path, dict(snapshot))
            time.sleep(0.25)
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        httpd.shutdown()
        stop_path.unlink(missing_ok=True)
        reset_path.unlink(missing_ok=True)
        analyze_path.unlink(missing_ok=True)
        ports_path.unlink(missing_ok=True)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
