from __future__ import annotations

from typing import Any

from ingest_farm.config import get_settings
from ingest_farm.pipeline.stages.base import ProcessingStage
from ingest_farm.schemas import ChannelConfig


class PassthroughStage(ProcessingStage):
    """Phase 1: queue only — preserves MPEG-TS without demux."""

    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        from gi.repository import Gst

        settings = get_settings()
        queue = Gst.ElementFactory.make("queue", "passthrough-queue")
        if queue is None:
            raise RuntimeError("GStreamer element 'queue' not available")

        settings.configure_queue(queue, "passthrough")

        ctx["pipeline"].add(queue)
        if not upstream.link(queue):
            raise RuntimeError("Failed to link source to passthrough queue")
        return queue
