from __future__ import annotations

from typing import Any

from ingest_farm.pipeline.stages.base import OutputStage, ProcessingStage, SourceStage
from ingest_farm.schemas import ChannelConfig, PipelineProfile, SourceProtocol


class PipelineBuilder:
    """Compose source → processing → output stages from a channel profile."""

    def __init__(self) -> None:
        from ingest_farm.pipeline.stages.output.ts_capture import TsCaptureStage
        from ingest_farm.pipeline.stages.processing.passthrough import PassthroughStage
        from ingest_farm.pipeline.stages.source import FileSourceStage, SrtSourceStage, UdpSourceStage

        self._sources: dict[SourceProtocol, SourceStage] = {
            SourceProtocol.SRT: SrtSourceStage(),
            SourceProtocol.UDP: UdpSourceStage(),
            SourceProtocol.FILE: FileSourceStage(),
        }
        self._processing: dict[PipelineProfile, ProcessingStage] = {
            PipelineProfile.TS_PASSTHROUGH: PassthroughStage(),
        }
        self._outputs: dict[PipelineProfile, OutputStage] = {
            PipelineProfile.TS_PASSTHROUGH: TsCaptureStage(),
        }

    def supported_protocols(self) -> list[str]:
        return [p.value for p in self._sources]

    def supported_profiles(self) -> list[str]:
        return [p.value for p in self._processing]

    def compose(self, config: ChannelConfig, ctx: dict[str, Any] | None = None):
        from gi.repository import Gst

        ctx = ctx or {}
        profile = config.pipeline.profile

        source_stage = self._sources.get(config.source.protocol)
        if source_stage is None:
            raise ValueError(f"Unsupported source protocol: {config.source.protocol}")

        processing_stage = self._processing.get(profile)
        if processing_stage is None:
            raise ValueError(f"Unsupported pipeline profile: {profile}")

        output_stage = self._outputs.get(profile)
        if output_stage is None:
            raise ValueError(f"No output stage for profile: {profile}")

        pipeline = Gst.Pipeline.new(f"ingest-{config.name}")
        ctx["pipeline"] = pipeline

        tail = source_stage.link(pipeline, config, ctx)
        tail = processing_stage.link(tail, config, ctx)
        output_stage.link(tail, config, ctx)
        return pipeline
