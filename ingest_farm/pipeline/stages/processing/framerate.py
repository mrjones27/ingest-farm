from __future__ import annotations

from typing import Any

from ingest_farm.pipeline.stages.base import ProcessingStage
from ingest_farm.schemas import ChannelConfig


class FrameRateStage(ProcessingStage):
    """Future: insert a registered frame-rate converter (e.g. Insync) after decode."""

    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        converter = (config.pipeline.video or {}).get("framerate", {}).get("converter")
        raise NotImplementedError(
            f"Frame-rate conversion is not implemented yet (requested: {converter}). "
            "Register an adapter in pipeline/encoders/external/."
        )
