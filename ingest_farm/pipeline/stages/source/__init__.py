from __future__ import annotations

from typing import Any

from ingest_farm.pipeline.stages.base import SourceStage
from ingest_farm.schemas import ChannelConfig


class SrtSourceStage(SourceStage):
    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        from gi.repository import Gst

        src = Gst.ElementFactory.make("srtsrc", "source")
        if src is None:
            raise RuntimeError("GStreamer element 'srtsrc' not available — install gst-plugins-bad")

        uri = config.source.uri
        if not uri.startswith("srt://"):
            uri = f"srt://{uri}"
        src.set_property("uri", uri)

        for key, value in config.source.config.items():
            if src.find_property(key):
                src.set_property(key, value)

        ctx["pipeline"].add(src)
        return src


class UdpSourceStage(SourceStage):
    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        from gi.repository import Gst

        src = Gst.ElementFactory.make("udpsrc", "source")
        if src is None:
            raise RuntimeError("GStreamer element 'udpsrc' not available")

        uri = config.source.uri.replace("udp://", "")
        if ":" in uri:
            host, port = uri.rsplit(":", 1)
            src.set_property("port", int(port))
            if host and host not in ("0.0.0.0", ""):
                src.set_property("address", host)
        else:
            src.set_property("port", int(uri))

        caps_str = config.source.config.get("caps", "video/mpegts")
        src.set_property("caps", Gst.Caps.from_string(caps_str))

        ctx["pipeline"].add(src)
        return src


class FileSourceStage(SourceStage):
    """Test/dev source — reads a MPEG-TS file."""

    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        from gi.repository import Gst

        src = Gst.ElementFactory.make("filesrc", "source")
        tsparse = Gst.ElementFactory.make("tsparse", "tsparse")
        if src is None or tsparse is None:
            raise RuntimeError("filesrc/tsparse GStreamer elements not available")

        src.set_property("location", config.source.uri)
        ctx["pipeline"].add(src)
        ctx["pipeline"].add(tsparse)
        if not src.link(tsparse):
            raise RuntimeError("Failed to link filesrc to tsparse")
        return tsparse
