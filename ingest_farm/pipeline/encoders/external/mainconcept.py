from __future__ import annotations

from typing import Any

from ingest_farm.pipeline.encoders.base import EncoderPlugin


class MainConceptH264Encoder(EncoderPlugin):
    """Adapter stub for MainConcept SDK (custom GstElement or appsink/appsrc bridge)."""

    name = "mainconcept:h264"

    def build_video_element(self, config: dict[str, Any]):
        raise NotImplementedError(
            "MainConcept SDK adapter is not installed. "
            "Implement GstElement wrapping or appsink/appsrc bridge here."
        )

    def build_audio_element(self, config: dict[str, Any]):
        raise NotImplementedError("MainConcept audio encoder not configured")
