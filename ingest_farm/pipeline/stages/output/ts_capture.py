from __future__ import annotations

from pathlib import Path
from typing import Any

from ingest_farm.pipeline.stages.base import OutputStage
from ingest_farm.schemas import ChannelConfig


class TsCaptureStage(OutputStage):
    """Write incoming MPEG-TS to segmented .ts files without remuxing.

    Uses tsparse for packet alignment and multifilesink for time-based split
    so the captured bytes stay as transport-stream (no demux/re-encode).
    """

    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        from gi.repository import Gst

        tsparse = Gst.ElementFactory.make("tsparse", "ts-align")
        sink = Gst.ElementFactory.make("multifilesink", "ts-capture")
        if tsparse is None or sink is None:
            raise RuntimeError("tsparse/multifilesink GStreamer elements not available")

        tsparse.set_property("set-timestamps", True)
        if tsparse.find_property("alignment"):
            tsparse.set_property("alignment", 7)

        output_dir = Path(ctx["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        location = str(output_dir / "segment_%05d.ts")
        segment_ns = config.pipeline.segment_duration_sec * Gst.SECOND

        sink.set_property("location", location)
        sink.set_property("next-file", 5)  # max-duration
        sink.set_property("max-file-duration", segment_ns)
        sink.set_property("post-messages", True)

        pipeline = ctx["pipeline"]
        pipeline.add(tsparse)
        pipeline.add(sink)
        if not upstream.link(tsparse):
            raise RuntimeError("Failed to link processing to tsparse")
        if not tsparse.link(sink):
            raise RuntimeError("Failed to link tsparse to multifilesink")

        ctx["segment_location"] = location
        return sink
