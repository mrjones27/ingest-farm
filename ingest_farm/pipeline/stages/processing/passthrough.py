from __future__ import annotations

from typing import Any

from ingest_farm.pipeline.stages.base import ProcessingStage
from ingest_farm.schemas import ChannelConfig


class PassthroughStage(ProcessingStage):
    """Phase 1: queue only — preserves MPEG-TS without demux."""

    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        from gi.repository import Gst

        queue = Gst.ElementFactory.make("queue", "passthrough-queue")
        if queue is None:
            raise RuntimeError("GStreamer element 'queue' not available")

        queue.set_property("max-size-time", 2 * Gst.SECOND)
        queue.set_property("max-size-buffers", 0)
        queue.set_property("max-size-bytes", 0)
        # Never block the SRT listener if the tee/preview stalls.
        queue.set_property("leaky", 2)  # downstream

        ctx["pipeline"].add(queue)
        if not upstream.link(queue):
            raise RuntimeError("Failed to link source to passthrough queue")
        return queue
