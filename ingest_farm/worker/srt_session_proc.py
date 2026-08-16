"""Isolated SRT live session process.

In-worker and compose_live paths abort libsrt on caller accept (gst 1.24).
A parse_launch tee graph (srtsrc → queue → tee → fakesinks) survives; run that
in a child process and control record via a state directory.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 3:
        print(
            "usage: python -m ingest_farm.worker.srt_session_proc "
            "<uri> <preview_dir> <state_dir>",
            file=sys.stderr,
        )
        return 2

    uri, preview_raw, state_raw = args[0], args[1], args[2]
    preview_dir = Path(preview_raw)
    state_dir = Path(state_raw)
    state_dir.mkdir(parents=True, exist_ok=True)
    ready_path = state_dir / "ready"
    cmd_path = state_dir / "cmd"
    status_path = state_dir / "status.json"
    cmd_path.unlink(missing_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst

    Gst.init(None)

    if not uri.startswith("srt://"):
        uri = f"srt://{uri}"
    if "latency=" not in uri:
        uri = f"{uri}{'&' if '?' in uri else '?'}latency=500"

    # Surviving graph from in-container bisect (compose_live segfaults on accept).
    desc = (
        f'srtsrc name=source uri="{uri}" wait-for-connection=true '
        "keep-listening=true authentication=false ! "
        "queue name=passthrough leaky=downstream max-size-time=2000000000 ! "
        "tee name=t "
        "t. ! queue name=preview-q leaky=downstream max-size-buffers=8 ! "
        "fakesink name=preview async=false sync=false "
        "t. ! queue name=record-q ! valve name=record-valve drop=true ! "
        "fakesink name=ts-capture-idle async=false sync=false"
    )
    print("launch", desc, flush=True)
    pipeline = Gst.parse_launch(desc)
    if pipeline is None:
        print("parse_launch failed", file=sys.stderr)
        return 1

    valve = pipeline.get_by_name("record-valve")
    idle_sink = pipeline.get_by_name("ts-capture-idle")
    # Create multifilesink off-pipeline until record starts (same as LiveTeeStage).
    record_sink = Gst.ElementFactory.make("multifilesink", "ts-capture")
    if record_sink is None:
        print("multifilesink unavailable", file=sys.stderr)
        return 1
    placeholder = preview_dir / "_idle" / "idle_%05d.ts"
    placeholder.parent.mkdir(parents=True, exist_ok=True)
    record_sink.set_property("location", str(placeholder))
    record_sink.set_property("next-file", 4)  # max-size
    record_sink.set_property("max-file-size", 8 * 1024 * 1024 * 1024)
    record_sink.set_property("post-messages", True)
    if record_sink.find_property("sync"):
        record_sink.set_property("sync", False)
    if record_sink.find_property("async"):
        record_sink.set_property("async", False)

    has_data = False
    buf_count = 0

    def _on_buf(_pad, _info):  # noqa: ANN001
        nonlocal has_data, buf_count
        has_data = True
        buf_count += 1
        if buf_count == 1 or buf_count % 100 == 0:
            print(f"srt-child buffers={buf_count}", flush=True)
        return Gst.PadProbeReturn.OK

    src = pipeline.get_by_name("source")
    if src is not None:
        pad = src.get_static_pad("src")
        if pad is not None:
            pad.add_probe(Gst.PadProbeType.BUFFER, _on_buf)

    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        print("failed to start pipeline", file=sys.stderr)
        return 1

    loop = GLib.MainLoop()
    recording = False

    def _write_status() -> None:
        try:
            status_path.write_text(
                json.dumps(
                    {"receiving": has_data, "recording": recording, "alive": True}
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _start_record(output_dir: Path) -> None:
        nonlocal recording
        if valve is None or record_sink is None or idle_sink is None:
            return
        output_dir.mkdir(parents=True, exist_ok=True)
        record_sink.set_property("location", str(output_dir / "segment_%05d.ts"))
        try:
            valve.unlink(idle_sink)
        except Exception:
            pass
        if record_sink.get_parent() is None:
            pipeline.add(record_sink)
        srcpad = valve.get_static_pad("src")
        if srcpad is not None and not srcpad.is_linked():
            if not valve.link(record_sink):
                return
        record_sink.sync_state_with_parent()
        valve.set_property("drop", False)
        recording = True

    def _stop_record() -> None:
        nonlocal recording
        if valve is None or record_sink is None or idle_sink is None:
            recording = False
            return
        valve.set_property("drop", True)
        try:
            valve.unlink(record_sink)
        except Exception:
            pass
        record_sink.set_state(Gst.State.NULL)
        srcpad = valve.get_static_pad("src")
        if srcpad is not None and not srcpad.is_linked():
            valve.link(idle_sink)
            idle_sink.sync_state_with_parent()
        recording = False

    def _poll_cmd() -> bool:
        _write_status()
        if not cmd_path.exists():
            return True
        try:
            raw = cmd_path.read_text(encoding="utf-8").strip()
            cmd_path.unlink(missing_ok=True)
        except OSError:
            return True
        if not raw:
            return True
        parts = raw.split(maxsplit=1)
        op = parts[0]
        if op == "quit":
            if recording:
                _stop_record()
            pipeline.set_state(Gst.State.NULL)
            loop.quit()
            return False
        if op == "record" and len(parts) == 2:
            _start_record(Path(parts[1]))
        elif op == "stop":
            _stop_record()
        return True

    GLib.timeout_add_seconds(1, _poll_cmd)
    ready_path.write_text("ok", encoding="utf-8")
    _write_status()
    print("srt-child ready", flush=True)
    loop.run()
    ready_path.unlink(missing_ok=True)
    try:
        status_path.write_text(json.dumps({"alive": False}), encoding="utf-8")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
