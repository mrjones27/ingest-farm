"""Isolated SRT live session process.

In-worker compose_live aborts libsrt on caller accept (gst 1.24).
Run srtsrc in a child process and control record via a state directory.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def _normalize_srt_uri(uri: str) -> str:
    from ingest_farm.config import get_settings

    if not uri.startswith("srt://"):
        uri = f"srt://{uri}"
    if '"' in uri or "!" in uri:
        raise ValueError("invalid SRT uri")
    return get_settings().ensure_srt_latency(uri)


def _configure_record_sink(record_sink, segment_duration_sec: int, placeholder: Path) -> None:
    from gi.repository import Gst

    record_sink.set_property("location", str(placeholder))
    segment_ns = max(1, segment_duration_sec) * Gst.SECOND
    record_sink.set_property("next-file", 5)  # max-duration
    record_sink.set_property("max-file-duration", segment_ns)
    record_sink.set_property("post-messages", True)
    if record_sink.find_property("sync"):
        record_sink.set_property("sync", False)
    if record_sink.find_property("async"):
        record_sink.set_property("async", False)


def _attach_fakesink_preview(pipeline, tee) -> None:
    from gi.repository import Gst

    from ingest_farm.config import get_settings

    settings = get_settings()
    prev_queue = Gst.ElementFactory.make("queue", "preview-q")
    prev_sink = Gst.ElementFactory.make("fakesink", "preview")
    if not all([prev_queue, prev_sink]):
        raise RuntimeError("preview elements unavailable")
    settings.configure_queue(prev_queue, "preview")
    prev_sink.set_property("sync", False)
    if prev_sink.find_property("async"):
        prev_sink.set_property("async", False)
    pipeline.add(prev_queue)
    pipeline.add(prev_sink)
    tee_prev = tee.get_request_pad("src_%u")
    prev_pad = prev_queue.get_static_pad("sink")
    if tee_prev is None or prev_pad is None or tee_prev.link(prev_pad) != Gst.PadLinkReturn.OK:
        raise RuntimeError("Failed to link tee → preview")
    if not prev_queue.link(prev_sink):
        raise RuntimeError("Failed to link preview queue")


# A source is only "receiving" while packets are actually arriving. Anything
# longer than this gap means the encoder went away, even if it never sent EOS.
_RECEIVING_IDLE_SEC = 2.0


def _read_srtsrc_stats(src) -> dict:
    from ingest_farm.common.gst_stats import gst_structure_to_dict, normalize_srt_stats

    if src is None or not src.find_property("stats"):
        return {}
    try:
        raw = src.get_property("stats")
    except Exception:
        return {}
    data = gst_structure_to_dict(raw)
    return normalize_srt_stats(data)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 3:
        print(
            "usage: python -m ingest_farm.worker.srt_session_proc "
            "<uri> <preview_dir> <state_dir> [segment_duration_sec]",
            file=sys.stderr,
        )
        return 2

    uri_raw, preview_raw, state_raw = args[0], args[1], args[2]
    segment_duration_sec = int(args[3]) if len(args) > 3 else 3600
    preview_dir = Path(preview_raw)
    state_dir = Path(state_raw)
    state_dir.mkdir(parents=True, exist_ok=True)
    ready_path = state_dir / "ready"
    cmds_path = state_dir / "cmds"
    status_path = state_dir / "status.json"
    cmds_path.write_text("", encoding="utf-8")
    preview_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = preview_dir / "thumb.jpg"

    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst

    # Gst.init() MUST run before anything that pulls in pydantic (config, and the
    # stage modules that import it). Importing pydantic first leaves srtsrc's
    # listener unable to complete srt_accept: callers connect, are never accepted,
    # and every encoder times out and reconnects forever.
    Gst.init(None)

    from ingest_farm.config import get_settings
    from ingest_farm.pipeline.stages.output.preview import attach_jpeg_preview_branch, relink_valve

    settings = get_settings()

    try:
        uri = _normalize_srt_uri(uri_raw)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    pipeline = Gst.Pipeline.new("srt-child")
    src = Gst.ElementFactory.make("srtsrc", "source")
    passthrough = Gst.ElementFactory.make("queue", "passthrough")
    tee = Gst.ElementFactory.make("tee", "t")
    rec_queue = Gst.ElementFactory.make("queue", "record-q")
    tsparse = Gst.ElementFactory.make("tsparse", "ts-align")
    valve = Gst.ElementFactory.make("valve", "record-valve")
    idle_sink = Gst.ElementFactory.make("fakesink", "ts-capture-idle")
    if not all([src, passthrough, tee, rec_queue, tsparse, valve, idle_sink]):
        print("required elements unavailable", file=sys.stderr)
        return 1

    src.set_property("uri", uri)
    src.set_property("wait-for-connection", settings.gst_srt_wait_for_connection)
    if src.find_property("keep-listening"):
        src.set_property("keep-listening", settings.gst_srt_keep_listening)
    if src.find_property("authentication"):
        src.set_property("authentication", settings.gst_srt_authentication)

    def _prop(name: str):
        return src.get_property(name) if src.find_property(name) else "n/a"

    print(
        f"srt-child uri={uri} wait-for-connection={_prop('wait-for-connection')} "
        f"keep-listening={_prop('keep-listening')} authentication={_prop('authentication')} "
        f"latency={_prop('latency')}",
        flush=True,
    )

    settings.configure_queue(passthrough, "passthrough")
    settings.configure_tee(tee)
    rec_queue.set_property("max-size-time", 0)
    rec_queue.set_property("max-size-buffers", 0)
    rec_queue.set_property("max-size-bytes", 0)
    settings.configure_tsparse(tsparse)
    valve.set_property("drop", True)
    idle_sink.set_property("sync", False)
    if idle_sink.find_property("async"):
        idle_sink.set_property("async", False)

    for el in (src, passthrough, tee, rec_queue, tsparse, valve, idle_sink):
        pipeline.add(el)

    if not src.link(passthrough) or not passthrough.link(tee):
        print("failed to link source → tee", file=sys.stderr)
        return 1

    tee_rec = tee.get_request_pad("src_%u")
    rec_pad = rec_queue.get_static_pad("sink")
    if tee_rec is None or rec_pad is None or tee_rec.link(rec_pad) != Gst.PadLinkReturn.OK:
        print("failed to link tee → record", file=sys.stderr)
        return 1
    if not rec_queue.link(tsparse) or not tsparse.link(valve) or not valve.link(idle_sink):
        print("failed to link record branch", file=sys.stderr)
        return 1

    preview_mode = "jpeg"
    if not attach_jpeg_preview_branch(pipeline, tee, preview_dir, thumb_path):
        _attach_fakesink_preview(pipeline, tee)
        preview_mode = "fakesink"
        print("srt-child preview fallback to fakesink", flush=True)
    else:
        print("srt-child preview jpeg (IDR) attached", flush=True)

    record_sink = Gst.ElementFactory.make("multifilesink", "ts-capture")
    if record_sink is None:
        print("multifilesink unavailable", file=sys.stderr)
        return 1
    placeholder = preview_dir / "_idle" / "idle_%05d.ts"
    placeholder.parent.mkdir(parents=True, exist_ok=True)
    _configure_record_sink(record_sink, segment_duration_sec, placeholder)

    has_data = False
    buf_count = 0
    bytes_total = 0
    rate_window = {"t": time.monotonic(), "bytes": 0, "mbps": 0.0}
    last_buf_at = {"t": 0.0}

    def _on_buf(_pad, info):  # noqa: ANN001
        nonlocal has_data, buf_count, bytes_total
        has_data = True
        buf_count += 1
        buf = info.get_buffer()
        size = buf.get_size() if buf is not None else 0
        bytes_total += size
        now = time.monotonic()
        # Restart the window after a gap so a resumed stream reports its real
        # rate instead of averaging across the dead air.
        if now - last_buf_at["t"] > _RECEIVING_IDLE_SEC:
            rate_window["t"] = now
            rate_window["bytes"] = 0
        last_buf_at["t"] = now
        elapsed = now - rate_window["t"]
        rate_window["bytes"] += size
        if elapsed >= 1.0:
            rate_window["mbps"] = (rate_window["bytes"] * 8) / elapsed / 1_000_000
            rate_window["t"] = now
            rate_window["bytes"] = 0
        if buf_count == 1 or buf_count % 100 == 0:
            print(f"srt-child buffers={buf_count} rate={rate_window['mbps']:.2f}Mb/s", flush=True)
        return Gst.PadProbeReturn.OK

    src_pad = src.get_static_pad("src")
    if src_pad is not None:
        src_pad.add_probe(Gst.PadProbeType.BUFFER, _on_buf)

    callers = {"n": 0}

    def _on_caller_added(_elem, *_args) -> None:
        callers["n"] += 1
        print(f"srt-child caller-added (n={callers['n']})", flush=True)

    def _on_caller_removed(_elem, *_args) -> None:
        callers["n"] = max(0, callers["n"] - 1)
        print(f"srt-child caller-removed (n={callers['n']})", flush=True)

    for signal_name, handler in (
        ("caller-added", _on_caller_added),
        ("caller-removed", _on_caller_removed),
    ):
        try:
            src.connect(signal_name, handler)
        except TypeError:
            pass

    # Without a bus watch every element error is silent and the child just
    # sits there looking healthy while nothing is read from the socket.
    pipeline_state = {"name": "NULL"}

    def _on_bus(_bus, message) -> bool:
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"srt-child ERROR from {message.src.get_name()}: {err} ({debug})", flush=True)
        elif message.type == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            print(f"srt-child WARN from {message.src.get_name()}: {err} ({debug})", flush=True)
        elif message.type == Gst.MessageType.EOS:
            print("srt-child EOS", flush=True)
        elif message.type == Gst.MessageType.STATE_CHANGED and message.src is pipeline:
            _old, new, _pending = message.parse_state_changed()
            pipeline_state["name"] = Gst.Element.state_get_name(new)
            print(f"srt-child pipeline state → {pipeline_state['name']}", flush=True)
        return True

    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", _on_bus)

    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        print("failed to start pipeline", file=sys.stderr)
        return 1
    print(f"srt-child set_state(PLAYING) → {ret.value_nick}", flush=True)

    loop = GLib.MainLoop()
    recording = False

    def _write_status() -> None:
        live = has_data and (time.monotonic() - last_buf_at["t"]) < _RECEIVING_IDLE_SEC
        payload: dict = {
            "available": True,
            "receiving": live,
            "recording": recording,
            "alive": True,
            "preview": preview_mode,
            "protocol": "srt",
            "pipeline-state": pipeline_state["name"],
            "caller-count": callers["n"],
        }
        try:
            srt = _read_srtsrc_stats(src)
            if srt:
                payload.update(srt)
                payload["available"] = True
        except Exception:
            pass
        # SRT socket counters belong to the current caller and reset to zero the
        # moment it drops, so the pad probe stays authoritative for the session.
        payload["bytes-received-total"] = bytes_total
        payload["receive-rate-mbps"] = round(rate_window["mbps"], 3) if live else 0.0
        try:
            status_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            pass

    def _start_record(output_dir: Path) -> None:
        nonlocal recording
        if valve is None or record_sink is None:
            print("record start missing valve/sink", flush=True)
            return
        output_dir.mkdir(parents=True, exist_ok=True)
        record_sink.set_property("location", str(output_dir / "segment_%05d.ts"))
        if not relink_valve(valve, record_sink, pipeline, Gst):
            print("record start failed to link multifilesink", flush=True)
            return
        valve.set_property("drop", False)
        recording = True
        print(f"record start → {output_dir}", flush=True)

    def _stop_record() -> None:
        nonlocal recording
        if valve is None or idle_sink is None:
            recording = False
            return
        valve.set_property("drop", True)
        relink_valve(valve, idle_sink, pipeline, Gst)
        recording = False
        print("record stop", flush=True)

    def _drain_cmds() -> bool:
        if not cmds_path.exists():
            return True
        try:
            raw = cmds_path.read_text(encoding="utf-8")
            if not raw.strip():
                return True
            cmds_path.write_text("", encoding="utf-8")
        except OSError:
            return True
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
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

    def _poll_cmd() -> bool:
        _write_status()
        return _drain_cmds()

    GLib.timeout_add(250, _poll_cmd)
    ready_path.write_text("ok", encoding="utf-8")
    _write_status()
    print("srt-child ready", flush=True)
    loop.run()
    ready_path.unlink(missing_ok=True)
    try:
        status_path.write_text(
            json.dumps({"alive": False, "available": False, "preview": preview_mode}),
            encoding="utf-8",
        )
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
