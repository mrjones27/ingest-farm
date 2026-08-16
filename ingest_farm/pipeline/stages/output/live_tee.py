"""Live tee: preview + gated record without blocking the SRT listener."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ingest_farm.pipeline.stages.base import OutputStage
from ingest_farm.schemas import ChannelConfig


class LiveTeeStage(OutputStage):
    """Tee live MPEG-TS into a preview branch and a gated record branch.

    Both branches use ``fakesink`` while connected/idle. ``multifilesink`` is
    only linked when recording starts — an idle multifilesink on the tee
    aborted the worker on the first SRT caller (GStreamer 1.24 / libsrt).
    """

    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        from gi.repository import Gst

        pipeline = ctx["pipeline"]
        preview_dir = Path(ctx["preview_dir"])
        preview_dir.mkdir(parents=True, exist_ok=True)
        thumb_path = preview_dir / "thumb.jpg"

        tee = Gst.ElementFactory.make("tee", "live-tee")
        if tee is None:
            raise RuntimeError("GStreamer element 'tee' not available")
        pipeline.add(tee)
        if not upstream.link(tee):
            raise RuntimeError("Failed to link processing to tee")

        # --- record branch (gated) ---
        rec_queue = Gst.ElementFactory.make("queue", "record-queue")
        valve = Gst.ElementFactory.make("valve", "record-valve")
        idle_sink = Gst.ElementFactory.make("fakesink", "ts-capture-idle")
        sink = Gst.ElementFactory.make("multifilesink", "ts-capture")
        if not all([rec_queue, valve, idle_sink, sink]):
            raise RuntimeError("Record branch elements not available")

        rec_queue.set_property("max-size-time", 0)
        rec_queue.set_property("max-size-buffers", 0)
        rec_queue.set_property("max-size-bytes", 0)
        valve.set_property("drop", True)
        idle_sink.set_property("sync", False)
        if idle_sink.find_property("async"):
            idle_sink.set_property("async", False)

        placeholder = preview_dir / "_idle" / "idle_%05d.ts"
        placeholder.parent.mkdir(parents=True, exist_ok=True)
        sink.set_property("location", str(placeholder))
        sink.set_property("next-file", 4)  # max-size
        sink.set_property("max-file-size", 8 * 1024 * 1024 * 1024)
        sink.set_property("post-messages", True)
        if sink.find_property("sync"):
            sink.set_property("sync", False)
        if sink.find_property("async"):
            sink.set_property("async", False)

        for el in (rec_queue, valve, idle_sink):
            pipeline.add(el)
        # Do not add multifilesink to the PLAYING pipeline until record starts.

        tee_rec = tee.get_request_pad("src_%u")
        rec_pad = rec_queue.get_static_pad("sink")
        if tee_rec is None or rec_pad is None or tee_rec.link(rec_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link tee → record queue")
        if not rec_queue.link(valve):
            raise RuntimeError("Failed to link record queue → valve")
        if not valve.link(idle_sink):
            raise RuntimeError("Failed to link valve → idle fakesink")

        # --- preview branch (non-decoding; JPEG preview re-enabled later) ---
        prev_queue = Gst.ElementFactory.make("queue", "preview-queue")
        prev_sink = Gst.ElementFactory.make("fakesink", "preview-fakesink")
        if not all([prev_queue, prev_sink]):
            raise RuntimeError("Preview branch elements not available")

        prev_queue.set_property("max-size-time", 0)
        prev_queue.set_property("max-size-bytes", 0)
        prev_queue.set_property("max-size-buffers", 8)
        prev_queue.set_property("leaky", 2)  # downstream
        prev_sink.set_property("sync", False)
        if prev_sink.find_property("async"):
            prev_sink.set_property("async", False)

        for el in (prev_queue, prev_sink):
            pipeline.add(el)

        tee_prev = tee.get_request_pad("src_%u")
        prev_pad = prev_queue.get_static_pad("sink")
        if tee_prev is None or prev_pad is None or tee_prev.link(prev_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link tee → preview queue")
        if not prev_queue.link(prev_sink):
            raise RuntimeError("Failed to link preview queue → fakesink")

        ctx["record_valve"] = valve
        ctx["record_sink"] = sink  # not yet in pipeline
        ctx["record_idle_sink"] = idle_sink
        ctx["thumb_path"] = thumb_path
        return idle_sink
