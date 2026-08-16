"""HLS proxy generation from MPEG-TS masters (GStreamer hlssink2)."""

from __future__ import annotations

import logging
from pathlib import Path

from ingest_farm.common.gst_utils import init_gstreamer, run_gst_pipeline
from ingest_farm.config import get_settings

logger = logging.getLogger(__name__)


def playlist_duration_ms(playlist: Path) -> int | None:
    """Sum #EXTINF durations from an HLS media playlist."""
    if not playlist.is_file():
        return None
    total = 0.0
    found = False
    for line in playlist.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("#EXTINF:"):
            continue
        payload = line.split(":", 1)[1]
        sec_str = payload.split(",", 1)[0].strip()
        try:
            total += float(sec_str)
        except ValueError:
            continue
        found = True
    if not found:
        return None
    return int(round(total * 1000))


def finalize_vod_playlist_text(text: str) -> str:
    """Force a completed recording playlist to VOD (PLAYLIST-TYPE + ENDLIST)."""
    lines: list[str] = []
    target_idx: int | None = None
    has_vod = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#EXT-X-ENDLIST"):
            continue
        if stripped.startswith("#EXT-X-PLAYLIST-TYPE:"):
            has_vod = True
            lines.append("#EXT-X-PLAYLIST-TYPE:VOD")
            continue
        if stripped.startswith("#EXT-X-TARGETDURATION"):
            target_idx = len(lines)
        lines.append(line)
    if not has_vod:
        insert_at = (target_idx + 1) if target_idx is not None else min(1, len(lines))
        lines.insert(insert_at, "#EXT-X-PLAYLIST-TYPE:VOD")
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def finalize_vod_playlist(playlist: Path) -> None:
    playlist.write_text(
        finalize_vod_playlist_text(playlist.read_text(encoding="utf-8", errors="replace")),
        encoding="utf-8",
    )


def rewrite_proxy_playlist(text: str, asset_id: str) -> str:
    """Rewrite relative segment URIs for the API and mark the playlist VOD."""
    rewritten: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("http"):
            rewritten.append(f"/api/assets/{asset_id}/proxy/{stripped}")
        else:
            rewritten.append(line)
    return finalize_vod_playlist_text("\n".join(rewritten) + "\n")


def generate_hls_proxy(master_path: Path, output_dir: Path) -> Path:
    """Transcode a recorded MPEG-TS master to a VOD HLS package for web preview."""
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    init_gstreamer()
    settings = get_settings()
    output_dir.mkdir(parents=True, exist_ok=True)
    playlist = output_dir / "playlist.m3u8"
    segment_pattern = output_dir / "segment_%05d.ts"

    pipeline = Gst.Pipeline.new("hls-proxy")
    filesrc = Gst.ElementFactory.make("filesrc", "src")
    tsparse = Gst.ElementFactory.make("tsparse", "tsparse")
    demux = Gst.ElementFactory.make("tsdemux", "demux")
    hlssink = Gst.ElementFactory.make("hlssink2", "hls")
    if not all([pipeline, filesrc, tsparse, demux, hlssink]):
        raise RuntimeError("Missing GStreamer elements for HLS proxy (filesrc/tsparse/tsdemux/hlssink2)")

    filesrc.set_property("location", str(master_path))
    settings.configure_tsparse(tsparse)
    # filesrc is not realtime; restamping from arrival time would crush duration.
    if tsparse.find_property("set-timestamps"):
        tsparse.set_property("set-timestamps", False)
    if demux.find_property("ignore-pcr"):
        demux.set_property("ignore-pcr", True)

    hlssink.set_property("location", str(segment_pattern))
    hlssink.set_property("playlist-location", str(playlist))
    hlssink.set_property("target-duration", int(settings.gst_proxy_hls_target_duration))
    hlssink.set_property("max-files", 0)
    if hlssink.find_property("playlist-length"):
        hlssink.set_property("playlist-length", 0)
    if hlssink.find_property("send-keyframe-requests"):
        hlssink.set_property("send-keyframe-requests", True)

    for element in (filesrc, tsparse, demux, hlssink):
        pipeline.add(element)
    if not filesrc.link(tsparse) or not tsparse.link(demux):
        raise RuntimeError("Failed to link filesrc → tsparse → tsdemux")

    video_linked = {"ok": False}

    def _attach_fakesink(pad) -> None:
        sink = Gst.ElementFactory.make("fakesink", None)
        if sink is None:
            return
        sink.set_property("sync", False)
        sink.set_property("async", False)
        pipeline.add(sink)
        sink.sync_state_with_parent()
        pad.link(sink.get_static_pad("sink"))

    def _set_prop(element, name: str, value) -> None:
        if not element.find_property(name):
            return
        try:
            element.set_property(name, value)
        except Exception:
            logger.warning("Could not set %s.%s=%r", element.get_name(), name, value)

    def _link_video(src_pad) -> None:
        if video_linked["ok"]:
            _attach_fakesink(src_pad)
            return

        vqueue = Gst.ElementFactory.make("queue", "vqueue")
        decodebin = Gst.ElementFactory.make("decodebin", "vdec")
        conv = Gst.ElementFactory.make("videoconvert", "vconv")
        scale = Gst.ElementFactory.make("videoscale", "vscale")
        caps = Gst.ElementFactory.make("capsfilter", "vcaps")
        x264 = Gst.ElementFactory.make("x264enc", "venc")
        parse = Gst.ElementFactory.make("h264parse", "vparse")
        outq = Gst.ElementFactory.make("queue", "outq")
        chain = (vqueue, decodebin, conv, scale, caps, x264, parse, outq)
        if any(el is None for el in chain):
            raise RuntimeError("Missing GStreamer elements for HLS video encode")

        vqueue.set_property("max-size-buffers", 0)
        vqueue.set_property("max-size-bytes", 0)
        vqueue.set_property("max-size-time", 0)
        outq.set_property("max-size-buffers", 0)
        outq.set_property("max-size-bytes", 0)
        outq.set_property("max-size-time", 0)
        caps.set_property(
            "caps",
            Gst.Caps.from_string(
                f"video/x-raw,width={int(settings.gst_proxy_width)},"
                f"height={int(settings.gst_proxy_height)}"
            ),
        )
        x264.set_property("bitrate", int(settings.gst_proxy_bitrate_kbps))
        x264.set_property("key-int-max", int(settings.gst_proxy_key_int_max))
        _set_prop(x264, "speed-preset", settings.gst_proxy_x264_speed_preset)
        _set_prop(x264, "byte-stream", True)
        _set_prop(x264, "aud", True)
        tune = (settings.gst_proxy_x264_tune or "").strip()
        if tune.lower() == "zerolatency":
            logger.warning("Ignoring GST_PROXY_X264_TUNE=zerolatency for VOD HLS proxy")
        elif tune:
            _set_prop(x264, "tune", tune)
        _set_prop(parse, "config-interval", 1)

        for el in chain:
            pipeline.add(el)
        if not (
            conv.link(scale)
            and scale.link(caps)
            and caps.link(x264)
            and x264.link(parse)
            and parse.link(outq)
        ):
            raise RuntimeError("Failed to link HLS video encode chain")

        hls_pad = hlssink.get_static_pad("video")
        if hls_pad is None:
            hls_pad = hlssink.get_request_pad("video")
        if hls_pad is None:
            raise RuntimeError("hlssink2 has no video pad")
        if outq.get_static_pad("src").link(hls_pad) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link encoder to hlssink2")

        def on_decoded(_decodebin, pad) -> None:
            caps_now = pad.get_current_caps() or pad.query_caps(None)
            struct = caps_now.get_structure(0) if caps_now and caps_now.get_size() else None
            media = struct.get_name() if struct else ""
            if media.startswith("video/"):
                sink_pad = conv.get_static_pad("sink")
                if sink_pad is not None and not sink_pad.is_linked():
                    pad.link(sink_pad)
            else:
                _attach_fakesink(pad)

        decodebin.connect("pad-added", on_decoded)
        if src_pad.link(vqueue.get_static_pad("sink")) != Gst.PadLinkReturn.OK:
            raise RuntimeError("Failed to link demux video pad to queue")
        if not vqueue.link(decodebin):
            dec_sink = decodebin.get_static_pad("sink")
            if dec_sink is None or vqueue.get_static_pad("src").link(dec_sink) != Gst.PadLinkReturn.OK:
                raise RuntimeError("Failed to link queue to decodebin")

        for el in chain:
            el.sync_state_with_parent()
        hlssink.sync_state_with_parent()
        video_linked["ok"] = True

    def on_demux_pad(_demux, pad) -> None:
        caps_now = pad.get_current_caps() or pad.query_caps(None)
        struct = caps_now.get_structure(0) if caps_now and caps_now.get_size() else None
        name = struct.get_name() if struct else ""
        pad_name = pad.get_name() or ""
        is_video = name.startswith("video/") or pad_name.startswith("video")
        if is_video:
            try:
                _link_video(pad)
            except Exception:
                logger.exception("HLS proxy video link failed")
                _attach_fakesink(pad)
            return
        _attach_fakesink(pad)

    demux.connect("pad-added", on_demux_pad)
    logger.info("Generating HLS proxy for %s → %s", master_path, playlist)
    run_gst_pipeline(pipeline, timeout_sec=float(settings.gst_proxy_timeout_sec))
    if not playlist.exists():
        raise RuntimeError(f"HLS playlist was not created: {playlist}")
    finalize_vod_playlist(playlist)
    return playlist
