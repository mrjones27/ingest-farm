from __future__ import annotations

from typing import Any

from ingest_farm.pipeline.stages.base import OutputStage
from ingest_farm.schemas import ChannelConfig


class RemuxStage(OutputStage):
    """Future: remux encoded essences into MKV/MXF/fMP4 via splitmuxsink."""

    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        raise NotImplementedError(
            "Remux output is not implemented yet. "
            "Use pipeline.profile: ts_passthrough for MPEG-TS capture."
        )
