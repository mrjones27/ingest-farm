from __future__ import annotations

from typing import Any

from ingest_farm.pipeline.encoders.base import FrameRateConverterPlugin


class InsyncFrameRateConverter(FrameRateConverterPlugin):
    """Adapter stub for Insync interpolated frame-rate conversion."""

    name = "insync"

    def build_element(self, config: dict[str, Any]):
        raise NotImplementedError(
            "Insync adapter is not installed. "
            "Implement as a ProcessingStage wrapping the Insync SDK."
        )
