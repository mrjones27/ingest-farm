from __future__ import annotations

from typing import Any

from ingest_farm.pipeline.stages.base import ProcessingStage
from ingest_farm.schemas import ChannelConfig


class DemuxStage(ProcessingStage):
    """Future: tsdemux + dynamic pad routing into encoder plugins."""

    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        raise NotImplementedError(
            "transcode_remux profile is not implemented yet. "
            "Use pipeline.profile: ts_passthrough for MPEG-TS capture."
        )
