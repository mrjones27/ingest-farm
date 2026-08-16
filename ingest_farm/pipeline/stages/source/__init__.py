from __future__ import annotations

from typing import Any

from ingest_farm.config import get_settings
from ingest_farm.pipeline.stages.base import SourceStage
from ingest_farm.schemas import ChannelConfig


def _add_mpegts_remux_bin(
    pipeline: Any,
    demux: Any,
    name: str = "mpegts-remux",
) -> Any:
    """Attach dynamic demux pads into mpegtsmux and return the muxer."""
    from gi.repository import Gst

    mux = Gst.ElementFactory.make("mpegtsmux", f"{name}-mux")
    if mux is None:
        raise RuntimeError("mpegtsmux not available")
    pipeline.add(mux)

    parsers: list[Any] = []

    def on_pad_added(_demux: Any, pad: Any) -> None:
        caps = pad.get_current_caps() or pad.query_caps(None)
        structure = caps.get_structure(0) if caps and caps.get_size() > 0 else None
        media = structure.get_name() if structure else ""

        parse = None
        if "video" in media or "h264" in media or "avc" in media:
            parse = Gst.ElementFactory.make("h264parse", None)
        elif "h265" in media or "hevc" in media:
            parse = Gst.ElementFactory.make("h265parse", None)
        elif "audio" in media or "mpeg" in media or "aac" in media:
            parse = Gst.ElementFactory.make("aacparse", None)
        else:
            # Unknown stream — try identity passthrough into mux.
            parse = Gst.ElementFactory.make("identity", None)

        if parse is None:
            return

        pipeline.add(parse)
        parse.sync_state_with_parent()
        parsers.append(parse)

        sink = parse.get_static_pad("sink")
        if pad.link(sink) != Gst.PadLinkReturn.OK:
            return

        mux_sink = mux.request_pad_simple("sink_%u")
        if mux_sink is None:
            mux_sink = mux.get_request_pad("sink_%d")
        src = parse.get_static_pad("src")
        if mux_sink is not None and src is not None:
            src.link(mux_sink)

    demux.connect("pad-added", on_pad_added)
    return mux


class SrtSourceStage(SourceStage):
    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        from gi.repository import Gst
        from urllib.parse import parse_qs, urlparse

        # Prefer ElementFactory + set_property (no parse_launch URI interpolation).
        settings = get_settings()
        uri = config.source.uri
        if not uri.startswith("srt://"):
            uri = f"srt://{uri}"
        if '"' in uri or "!" in uri:
            raise ValueError("Invalid SRT URI")
        parsed = urlparse(uri)
        query = parse_qs(parsed.query)
        has_latency = "latency" in query or "latency" in config.source.config
        if not has_latency:
            uri = settings.ensure_srt_latency(uri)
        elif "latency" not in query and "latency" in config.source.config:
            uri = f"{uri}{'&' if parsed.query else '?'}latency={config.source.config['latency']}"

        src = Gst.ElementFactory.make("srtsrc", "source")
        if src is None:
            raise RuntimeError("GStreamer element 'srtsrc' not available — install gst-plugins-bad")
        src.set_property("uri", uri)
        wait = config.source.config.get("wait_for_connection", settings.gst_srt_wait_for_connection)
        src.set_property("wait-for-connection", bool(wait))
        if src.find_property("keep-listening"):
            keep = config.source.config.get("keep_listening", settings.gst_srt_keep_listening)
            src.set_property("keep-listening", bool(keep))
        auth = config.source.config.get("authentication", settings.gst_srt_authentication)
        if src.find_property("authentication"):
            src.set_property("authentication", auth in (True, "true", "1", 1))
        ctx["pipeline"].add(src)

        ctx["source_element"] = src
        ctx["source_kind"] = "srt"
        return src


class UdpSourceStage(SourceStage):
    """UDP MPEG-TS (default) or RTP (h264 / mp2t) via source.config.transport."""

    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        from gi.repository import Gst

        pipeline = ctx["pipeline"]
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

        settings = get_settings()
        if settings.gst_udp_buffer_size > 0 and src.find_property("buffer-size"):
            src.set_property("buffer-size", int(settings.gst_udp_buffer_size))

        transport = (config.source.config.get("transport") or "mpegts").lower()
        pipeline.add(src)

        if transport in {"mpegts", "ts", "udp"}:
            caps_str = config.source.config.get("caps", "video/mpegts")
            src.set_property("caps", Gst.Caps.from_string(caps_str))
            return src

        # RTP transports remux into MPEG-TS for the passthrough capture profile.
        jitter = Gst.ElementFactory.make("rtpjitterbuffer", "rtp-jitter")
        if jitter is None:
            raise RuntimeError("rtpjitterbuffer not available")
        pipeline.add(jitter)
        if jitter.find_property("latency"):
            jitter.set_property("latency", int(settings.gst_rtp_jitterbuffer_ms))
        src.link(jitter)

        if transport in {"rtp-mp2t", "rtp-mpegts"}:
            caps = config.source.config.get(
                "caps",
                "application/x-rtp,media=application,encoding-name=MP2T,clock-rate=90000",
            )
            src.set_property("caps", Gst.Caps.from_string(caps))
            depay = Gst.ElementFactory.make("rtpmp2tdepay", "rtp-depay")
            if depay is None:
                raise RuntimeError("rtpmp2tdepay not available")
            pipeline.add(depay)
            jitter.link(depay)
            return depay

        if transport in {"rtp-h264", "rtp"}:
            caps = config.source.config.get(
                "caps",
                "application/x-rtp,media=video,encoding-name=H264,clock-rate=90000",
            )
            src.set_property("caps", Gst.Caps.from_string(caps))
            depay = Gst.ElementFactory.make("rtph264depay", "rtp-depay")
            parse = Gst.ElementFactory.make("h264parse", "rtp-h264parse")
            mux = Gst.ElementFactory.make("mpegtsmux", "rtp-mpegtsmux")
            if not all([depay, parse, mux]):
                raise RuntimeError("RTP H264 remux elements not available")
            pipeline.add(depay)
            pipeline.add(parse)
            pipeline.add(mux)
            jitter.link(depay)
            depay.link(parse)
            parse.link(mux)
            return mux

        raise ValueError(f"Unsupported UDP transport: {transport}")


class FileSourceStage(SourceStage):
    """Test/dev source — reads a MPEG-TS file."""

    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        from gi.repository import Gst

        src = Gst.ElementFactory.make("filesrc", "source")
        tsparse = Gst.ElementFactory.make("tsparse", "tsparse")
        if src is None or tsparse is None:
            raise RuntimeError("filesrc/tsparse GStreamer elements not available")

        src.set_property("location", config.source.uri)
        get_settings().configure_tsparse(tsparse)
        ctx["pipeline"].add(src)
        ctx["pipeline"].add(tsparse)
        if not src.link(tsparse):
            raise RuntimeError("Failed to link filesrc to tsparse")
        return tsparse


class RtmpSourceStage(SourceStage):
    """RTMP pull — demux FLV and remux to MPEG-TS without re-encode."""

    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        from gi.repository import Gst

        pipeline = ctx["pipeline"]
        src = Gst.ElementFactory.make("rtmpsrc", "source")
        if src is None:
            src = Gst.ElementFactory.make("rtmp2src", "source")
        if src is None:
            raise RuntimeError("rtmpsrc/rtmp2src not available")

        location = config.source.uri
        if src.find_property("location"):
            src.set_property("location", location)
        elif src.find_property("uri"):
            src.set_property("uri", location)

        demux = Gst.ElementFactory.make("flvdemux", "rtmp-demux")
        if demux is None:
            raise RuntimeError("flvdemux not available")

        pipeline.add(src)
        pipeline.add(demux)
        if not src.link(demux):
            raise RuntimeError("Failed to link rtmpsrc to flvdemux")

        return _add_mpegts_remux_bin(pipeline, demux, name="rtmp")


class HlsSourceStage(SourceStage):
    """HLS pull — demux playlist segments and remux to MPEG-TS without re-encode."""

    def link(self, upstream: Any, config: ChannelConfig, ctx: dict[str, Any]) -> Any:
        from gi.repository import Gst

        pipeline = ctx["pipeline"]
        # urisourcebin/hlsdemux path: souphttpsrc is used internally by hlsdemux in many builds.
        src = Gst.ElementFactory.make("souphttpsrc", "hls-http")
        demux = Gst.ElementFactory.make("hlsdemux", "hls-demux")
        if src is None or demux is None:
            raise RuntimeError("souphttpsrc/hlsdemux not available — install gst-plugins-bad")

        src.set_property("location", config.source.uri)
        if src.find_property("is-live"):
            src.set_property("is-live", True)

        pipeline.add(src)
        pipeline.add(demux)
        if not src.link(demux):
            raise RuntimeError("Failed to link souphttpsrc to hlsdemux")

        return _add_mpegts_remux_bin(pipeline, demux, name="hls")
