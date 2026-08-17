"""Live tee: JPEG preview (IDR only) + gated record without blocking the source."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ingest_farm.config import get_settings
from ingest_farm.pipeline.stages.base import OutputStage
from ingest_farm.pipeline.stages.output.preview import attach_jpeg_preview_branch
from ingest_farm.schemas import ChannelConfig


def _configure_record_multifilesink(sink: Any, config: ChannelConfig, placeholder: Path) -> None:
    from gi.repository import Gst

    sink.set_property("location", str(placeholder))
    segment_ns = max(1, int(config.pipeline.segment_duration_sec)) * Gst.SECOND
    sink.set_property("next-file", 5)  # max-duration (needs tsparse timestamps)
    sink.set_property("max-file-duration", segment_ns)
    sink.set_property("post-messages", True)
    if sink.find_property("sync"):
        sink.set_property("sync", False)
    if sink.find_property("async"):
        sink.set_property("async", False)


class LiveTeeStage(OutputStage):
    """Tee live MPEG-TS into an IDR JPEG preview and a gated record branch.

    ``multifilesink`` is only linked when recording starts. ``tsparse`` sits on
    the record branch so duration-based splits have timestamps.
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
        settings = get_settings()
        if tee.find_property("allow-not-linked"):
            tee.set_property("allow-not-linked", settings.gst_tee_allow_not_linked)
        pipeline.add(tee)
        if not upstream.link(tee):
            raise RuntimeError("Failed to link processing to tee")

        rec_queue = Gst.ElementFactory.make("queue", "record-queue")
        tsparse = Gst.ElementFactory.make("tsparse", "ts-align")
        valve = Gst.ElementFactory.make("valve", "record-valve")
        idle_sink = Gst.ElementFactory.make("fakesink", "ts-capture-idle")
        sink = Gst.ElementFactory.make("multifilesink", "ts-capture")
        if not all([rec_queue, tsparse, valve, idle_sink, sink]):
            raise RuntimeError("Record branch elements not available")

        rec_queue.set_property("max-size-time", 0)
        rec_queue.set_property("max-size-buffers", 0)
        rec_queue.set_property("max-size-bytes", 0)
        settings.configure_tsparse(tsparse)
        valve.set_property("drop", True)
        idle_sink.set_property("sync", False)
        if idle_sink.find_property("async"):
            idle_sink.set_property("async", False)

        placeholder = preview_dir / "_idle" / "idle_%05d.ts"
        placeholder.parent.mkdir(parents=True, exist_ok=True)
        _configure_record_multifilesink(sink, config, placeholder)

        for el in (rec_queue, tsparse, valve, idle_sink):
            pipeline.add(el)

        tee_rec = tee.get_request_pad("src_%u")
        rec_pad = rec_queue.get_static_pad("sink")
        if tee_rec is None or rec_pad is None or tee_rec.link(rec_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link tee → record queue")
        if not rec_queue.link(tsparse):
            raise RuntimeError("Failed to link record queue → tsparse")
        if not tsparse.link(valve):
            raise RuntimeError("Failed to link tsparse → valve")
        if not valve.link(idle_sink):
            raise RuntimeError("Failed to link valve → idle fakesink")

        jpeg_ok = attach_jpeg_preview_branch(pipeline, tee, preview_dir, thumb_path)
        if not jpeg_ok:
            prev_queue = Gst.ElementFactory.make("queue", "preview-queue")
            prev_sink = Gst.ElementFactory.make("fakesink", "preview-fakesink")
            if not all([prev_queue, prev_sink]):
                raise RuntimeError("Preview branch elements not available")
            settings.configure_queue(prev_queue, "preview")
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
            ctx["preview_mode"] = "fakesink"
        else:
            ctx["preview_mode"] = "jpeg"

        ctx["record_valve"] = valve
        ctx["record_sink"] = sink
        ctx["record_idle_sink"] = idle_sink
        ctx["thumb_path"] = thumb_path
        # Every protocol funnels through this tee, so its sink pad is the one
        # place a caller can observe whether media is actually arriving.
        ctx["live_tee"] = tee
        return idle_sink
