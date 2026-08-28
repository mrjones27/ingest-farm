"""JPEG live preview: decode IDR/keyframes only so thumbs are not mid-GOP garbage."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any


from ingest_farm.config import get_settings


def _set_skip_nonkey(dec: Any) -> None:
    if dec is None or not dec.find_property("skip-frame"):
        return
    try:
        dec.set_property("skip-frame", "skip-nonkey")
        return
    except Exception:
        pass
    try:
        dec.set_property("skip-frame", 4)
    except Exception:
        pass


def _drop_non_keyframes(gst: Any):
    def _probe(_pad: Any, info: Any) -> Any:
        buf = info.get_buffer()
        if buf is None:
            return gst.PadProbeReturn.OK
        flags = buf.get_flags() if hasattr(buf, "get_flags") else 0
        delta = getattr(gst.BufferFlags, "DELTA_UNIT", 0)
        if flags & delta:
            return gst.PadProbeReturn.DROP
        return gst.PadProbeReturn.OK

    return _probe


def write_thumbs(thumb_path: Path, data: bytes) -> None:
    """Publish JPEG bytes via atomic replace so readers never see a truncated file."""
    tmp = thumb_path.with_name("thumb.tmp.jpg")
    stable = thumb_path.with_name("thumb.ok.jpg")
    tmp.write_bytes(data)
    try:
        tmp.replace(stable)
    except OSError:
        stable.write_bytes(data)
    pub_tmp = thumb_path.with_name("thumb.jpg.tmp")
    try:
        pub_tmp.write_bytes(data)
        pub_tmp.replace(thumb_path)
    except OSError:
        try:
            thumb_path.write_bytes(data)
        except OSError:
            pass


def attach_jpeg_preview_branch(
    pipeline: Any,
    tee: Any,
    preview_dir: Path,
    thumb_path: Path,
    *,
    name_prefix: str = "preview",
) -> bool:
    """Attach tee → leaky queue → tsdemux → parse → IDR-only decode → jpeg.

    Returns False if core elements are unavailable (caller should fakesink).
    """
    from gi.repository import Gst

    settings = get_settings()
    preview_dir.mkdir(parents=True, exist_ok=True)

    prev_queue = Gst.ElementFactory.make("queue", f"{name_prefix}-queue")
    demux = Gst.ElementFactory.make("tsdemux", f"{name_prefix}-tsdemux")
    if not all([prev_queue, demux]):
        return False

    settings.configure_queue(prev_queue, "preview")

    pipeline.add(prev_queue)
    pipeline.add(demux)

    tee_prev = tee.get_request_pad("src_%u")
    prev_pad = prev_queue.get_static_pad("sink")
    if tee_prev is None or prev_pad is None or tee_prev.link(prev_pad) != Gst.PadLinkReturn.OK:
        return False
    if not prev_queue.link(demux):
        return False

    last_encode = {"t": 0.0}

    def on_pad_added(_demux: Any, pad: Any) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        structure = caps.get_structure(0) if caps and caps.get_size() > 0 else None
        media = (structure.get_name() if structure else "").lower()

        def _fakesink() -> None:
            fake = Gst.ElementFactory.make("fakesink", None)
            if fake is None:
                return
            fake.set_property("sync", False)
            if fake.find_property("async"):
                fake.set_property("async", False)
            pipeline.add(fake)
            fake.sync_state_with_parent()
            sinkpad = fake.get_static_pad("sink")
            if sinkpad is not None:
                pad.link(sinkpad)

        if "audio" in media:
            _fakesink()
            return

        is_h265 = "h265" in media or "hevc" in media
        is_h264 = "h264" in media or "avc" in media or "video" in media
        if not (is_h264 or is_h265):
            _fakesink()
            return

        parse = Gst.ElementFactory.make("h265parse" if is_h265 else "h264parse", None)
        dec = Gst.ElementFactory.make("avdec_h265" if is_h265 else "avdec_h264", None)
        dec_q = Gst.ElementFactory.make("queue", None)
        vconv = Gst.ElementFactory.make("videoconvert", None)
        vscale = Gst.ElementFactory.make("videoscale", None)
        capsfilter = Gst.ElementFactory.make("capsfilter", None)
        jpeg = Gst.ElementFactory.make("jpegenc", None)
        sink = Gst.ElementFactory.make("fakesink", None)
        if not all([parse, dec, dec_q, vconv, vscale, capsfilter, jpeg, sink]):
            _fakesink()
            return

        if parse.find_property("config-interval"):
            parse.set_property("config-interval", int(settings.gst_parse_config_interval))
        if settings.gst_thumb_idr_only:
            _set_skip_nonkey(dec)
        dec_q.set_property("leaky", settings.leaky("preview"))
        dec_q.set_property("max-size-buffers", 2)
        dec_q.set_property("max-size-bytes", 0)
        dec_q.set_property("max-size-time", 0)
        capsfilter.set_property(
            "caps",
            Gst.Caps.from_string(
                f"video/x-raw,width={int(settings.gst_thumb_width)},height={int(settings.gst_thumb_height)}"
            ),
        )
        if jpeg.find_property("quality"):
            jpeg.set_property("quality", int(settings.gst_thumb_jpeg_quality))
        sink.set_property("sync", False)
        if sink.find_property("async"):
            sink.set_property("async", False)

        for el in (parse, dec, dec_q, vconv, vscale, capsfilter, jpeg, sink):
            pipeline.add(el)

        sinkpad = parse.get_static_pad("sink")
        if sinkpad is None or pad.link(sinkpad) != Gst.PadLinkReturn.OK:
            return

        parse_src = parse.get_static_pad("src")
        if parse_src is not None and settings.gst_thumb_idr_only:
            parse_src.add_probe(Gst.PadProbeType.BUFFER, _drop_non_keyframes(Gst))

        if not parse.link(dec):
            return
        if not dec.link(dec_q):
            return
        if not dec_q.link(vconv):
            return
        if not vconv.link(vscale):
            return
        if not vscale.link(capsfilter):
            return
        if not capsfilter.link(jpeg):
            return
        if not jpeg.link(sink):
            return

        jpeg_src = jpeg.get_static_pad("src")
        if jpeg_src is not None:

            def _on_jpeg(_pad: Any, info: Any) -> Any:
                now = time.monotonic()
                if now - last_encode["t"] < float(settings.gst_thumb_interval_sec):
                    return Gst.PadProbeReturn.OK
                buf = info.get_buffer()
                if buf is None:
                    return Gst.PadProbeReturn.OK
                ok, mapped = buf.map(Gst.MapFlags.READ)
                if not ok:
                    return Gst.PadProbeReturn.OK
                try:
                    data = bytes(mapped.data)
                finally:
                    buf.unmap(mapped)
                if len(data) < 100:
                    return Gst.PadProbeReturn.OK
                try:
                    write_thumbs(thumb_path, data)
                    last_encode["t"] = now
                except OSError:
                    pass
                return Gst.PadProbeReturn.OK

            jpeg_src.add_probe(Gst.PadProbeType.BUFFER, _on_jpeg)

        for el in (parse, dec, dec_q, vconv, vscale, capsfilter, jpeg, sink):
            el.sync_state_with_parent()

    demux.connect("pad-added", on_pad_added)
    return True


def relink_valve(valve: Any, new_sink: Any, pipeline: Any, gst: Any) -> bool:
    """Unlink whatever is on valve src and link ``new_sink`` instead."""
    srcpad = valve.get_static_pad("src")
    if srcpad is None:
        return False
    peer = srcpad.get_peer()
    if peer is not None:
        old = peer.get_parent_element() if hasattr(peer, "get_parent_element") else None
        srcpad.unlink(peer)
        if old is not None:
            old.set_state(gst.State.NULL)
    if new_sink.get_parent() is None:
        pipeline.add(new_sink)
    if not valve.link(new_sink):
        return False
    new_sink.sync_state_with_parent()
    return True
