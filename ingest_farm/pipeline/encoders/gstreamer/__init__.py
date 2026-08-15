from __future__ import annotations

from typing import Any

from ingest_farm.pipeline.encoders.base import EncoderPlugin


class GStreamerX264Encoder(EncoderPlugin):
    name = "gstreamer:x264enc"

    def build_video_element(self, config: dict[str, Any]):
        from gi.repository import Gst

        enc = Gst.ElementFactory.make("x264enc", "video-enc")
        if enc is None:
            raise RuntimeError("x264enc not available")
        for key, value in config.items():
            if enc.find_property(key):
                enc.set_property(key, value)
        return enc

    def build_audio_element(self, config: dict[str, Any]):
        raise NotImplementedError("x264enc is video-only; use gstreamer:avenc_aac for audio")


class GStreamerAacEncoder(EncoderPlugin):
    name = "gstreamer:avenc_aac"

    def build_video_element(self, config: dict[str, Any]):
        raise NotImplementedError("avenc_aac is audio-only")

    def build_audio_element(self, config: dict[str, Any]):
        from gi.repository import Gst

        enc = Gst.ElementFactory.make("avenc_aac", "audio-enc")
        if enc is None:
            raise RuntimeError("avenc_aac not available")
        for key, value in config.items():
            if enc.find_property(key):
                enc.set_property(key, value)
        return enc
