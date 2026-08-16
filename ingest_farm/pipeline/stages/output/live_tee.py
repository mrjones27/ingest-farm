from __future__ import annotations

from pathlib import Path
from typing import Any

from ingest_farm.pipeline.stages.base import OutputStage
from ingest_farm.schemas import ChannelConfig


class LiveTeeStage(OutputStage):
    """Tee live MPEG-TS into a preview JPEG branch and a gated record branch.

    Record branch uses a ``valve`` (drop=true until recording starts) so the
    SRT/UDP source can stay connected without writing segments.
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
        sink = Gst.ElementFactory.make("multifilesink", "ts-capture")
        if not all([rec_queue, valve, sink]):
            raise RuntimeError("Record branch elements not available")

        rec_queue.set_property("max-size-time", 0)
        rec_queue.set_property("max-size-buffers", 0)
        rec_queue.set_property("max-size-bytes", 0)
        valve.set_property("drop", True)

        # Placeholder until start_recording sets the real session path.
        placeholder = preview_dir / "_idle" / "idle_%05d.ts"
        placeholder.parent.mkdir(parents=True, exist_ok=True)
        sink.set_property("location", str(placeholder))
        # Size-based split (not duration) so missing PTS cannot stall the sink.
        sink.set_property("next-file", 4)  # max-size
        sink.set_property("max-file-size", 8 * 1024 * 1024 * 1024)
        sink.set_property("post-messages", True)
        if sink.find_property("sync"):
            sink.set_property("sync", False)
        if sink.find_property("async"):
            sink.set_property("async", False)

        for el in (rec_queue, valve, sink):
            pipeline.add(el)

        tee_rec = tee.get_request_pad("src_%u")
        rec_sink = rec_queue.get_static_pad("sink")
        if tee_rec is None or rec_sink is None or tee_rec.link(rec_sink) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link tee → record queue")
        if not rec_queue.link(valve):
            raise RuntimeError("Failed to link record queue → valve")
        if not valve.link(sink):
            raise RuntimeError("Failed to link valve → multifilesink")

        # --- preview branch (always on while connected) ---
        prev_queue = Gst.ElementFactory.make("queue", "preview-queue")
        decode = Gst.ElementFactory.make("decodebin", "preview-decode")
        convert = Gst.ElementFactory.make("videoconvert", "preview-convert")
        scale = Gst.ElementFactory.make("videoscale", "preview-scale")
        capsfilter = Gst.ElementFactory.make("capsfilter", "preview-caps")
        jpeg = Gst.ElementFactory.make("jpegenc", "preview-jpeg")
        thumbsink = Gst.ElementFactory.make("multifilesink", "preview-thumb")
        if not all([prev_queue, decode, convert, scale, capsfilter, jpeg, thumbsink]):
            raise RuntimeError("Preview branch elements not available")

        # Preview must never block the tee: drop when decode can't keep up.
        prev_queue.set_property("max-size-time", 0)
        prev_queue.set_property("max-size-bytes", 0)
        prev_queue.set_property("max-size-buffers", 8)
        prev_queue.set_property("leaky", 2)  # downstream
        capsfilter.set_property(
            "caps",
            Gst.Caps.from_string("video/x-raw,width=480,height=270,framerate=5/1"),
        )
        if jpeg.find_property("quality"):
            jpeg.set_property("quality", 75)
        thumbsink.set_property("location", str(thumb_path))
        thumbsink.set_property("max-files", 1)
        if thumbsink.find_property("sync"):
            thumbsink.set_property("sync", False)
        if thumbsink.find_property("async"):
            thumbsink.set_property("async", False)

        rate = Gst.ElementFactory.make("videorate", "preview-rate")
        if rate is None:
            raise RuntimeError("videorate not available")
        if rate.find_property("drop-only"):
            rate.set_property("drop-only", True)
        if rate.find_property("skip-to-first"):
            rate.set_property("skip-to-first", True)

        for el in (prev_queue, decode, convert, scale, rate, capsfilter, jpeg, thumbsink):
            pipeline.add(el)

        tee_prev = tee.get_request_pad("src_%u")
        prev_sink = prev_queue.get_static_pad("sink")
        if tee_prev is None or prev_sink is None or tee_prev.link(prev_sink) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link tee → preview queue")
        if not prev_queue.link(decode):
            raise RuntimeError("Failed to link preview queue → decodebin")

        def _on_pad_added(_decode: Any, pad: Any) -> None:
            caps = pad.get_current_caps() or pad.query_caps(None)
            structure = caps.get_structure(0) if caps and caps.get_size() > 0 else None
            media = structure.get_name() if structure else ""
            # #region agent log
            from ingest_farm.common.agent_debug import agent_log

            agent_log(
                "C",
                "live_tee.py:_on_pad_added",
                "decodebin pad-added",
                {"media": media, "pad": pad.get_name()},
            )
            # #endregion
            # Unlinked decodebin pads (esp. audio) stall the whole preview branch.
            if media.startswith("audio/"):
                fakesink = Gst.ElementFactory.make("fakesink", None)
                if fakesink is None:
                    return
                fakesink.set_property("sync", False)
                if fakesink.find_property("async"):
                    fakesink.set_property("async", False)
                pipeline.add(fakesink)
                fakesink.sync_state_with_parent()
                sink_pad = fakesink.get_static_pad("sink")
                link_ret = pad.link(sink_pad) if sink_pad is not None else Gst.PadLinkReturn.WRONG_HIERARCHY
                # #region agent log
                agent_log(
                    "C",
                    "live_tee.py:_on_pad_added",
                    "preview audio → fakesink",
                    {"link_ok": link_ret == Gst.PadLinkReturn.OK, "link_ret": int(link_ret)},
                )
                # #endregion
                return
            if not media.startswith("video/"):
                fakesink = Gst.ElementFactory.make("fakesink", None)
                if fakesink is None:
                    return
                fakesink.set_property("sync", False)
                pipeline.add(fakesink)
                fakesink.sync_state_with_parent()
                sink_pad = fakesink.get_static_pad("sink")
                if sink_pad is not None:
                    pad.link(sink_pad)
                return
            sink_pad = convert.get_static_pad("sink")
            if sink_pad is not None and not sink_pad.is_linked():
                link_ret = pad.link(sink_pad)
                # #region agent log
                agent_log(
                    "C",
                    "live_tee.py:_on_pad_added",
                    "preview video link",
                    {"media": media, "link_ok": link_ret == Gst.PadLinkReturn.OK, "link_ret": int(link_ret)},
                )
                # #endregion
                if link_ret == Gst.PadLinkReturn.OK:
                    convert.sync_state_with_parent()
                    scale.sync_state_with_parent()
                    rate.sync_state_with_parent()
                    capsfilter.sync_state_with_parent()
                    jpeg.sync_state_with_parent()
                    thumbsink.sync_state_with_parent()

        decode.connect("pad-added", _on_pad_added)
        if not convert.link(scale):
            raise RuntimeError("Failed to link preview convert → scale")
        if not scale.link(rate):
            raise RuntimeError("Failed to link preview scale → videorate")
        if not rate.link(capsfilter):
            raise RuntimeError("Failed to link preview videorate → caps")
        if not capsfilter.link(jpeg):
            raise RuntimeError("Failed to link preview caps → jpegenc")
        if not jpeg.link(thumbsink):
            raise RuntimeError("Failed to link jpegenc → thumbsink")

        ctx["record_valve"] = valve
        ctx["record_sink"] = sink
        ctx["thumb_path"] = thumb_path
        return sink
