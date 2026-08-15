from __future__ import annotations

from ingest_farm.pipeline.encoders.base import EncoderPlugin, FrameRateConverterPlugin
from ingest_farm.pipeline.encoders.external import InsyncFrameRateConverter, MainConceptH264Encoder
from ingest_farm.pipeline.encoders.gstreamer import GStreamerAacEncoder, GStreamerX264Encoder


class EncoderRegistry:
    def __init__(self) -> None:
        self._encoders: dict[str, EncoderPlugin] = {}
        self._framerate: dict[str, FrameRateConverterPlugin] = {}

    def register_encoder(self, plugin: EncoderPlugin) -> None:
        self._encoders[plugin.name] = plugin

    def register_framerate(self, plugin: FrameRateConverterPlugin) -> None:
        self._framerate[plugin.name] = plugin

    def get_encoder(self, name: str) -> EncoderPlugin:
        if name not in self._encoders:
            available = ", ".join(sorted(self._encoders)) or "(none)"
            raise KeyError(f"Encoder '{name}' not registered. Available: {available}")
        return self._encoders[name]

    def get_framerate_converter(self, name: str) -> FrameRateConverterPlugin:
        if name not in self._framerate:
            available = ", ".join(sorted(self._framerate)) or "(none)"
            raise KeyError(f"Frame-rate converter '{name}' not registered. Available: {available}")
        return self._framerate[name]

    def list_encoders(self) -> list[str]:
        return sorted(self._encoders)

    def list_framerate_converters(self) -> list[str]:
        return sorted(self._framerate)


def default_registry() -> EncoderRegistry:
    registry = EncoderRegistry()
    for plugin in (
        GStreamerX264Encoder(),
        GStreamerAacEncoder(),
        MainConceptH264Encoder(),
    ):
        registry.register_encoder(plugin)
    registry.register_framerate(InsyncFrameRateConverter())
    return registry
