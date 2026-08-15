from __future__ import annotations

from pathlib import Path
from typing import Any


def init_gstreamer(plugin_path: str = "") -> None:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    if plugin_path:
        import os

        os.environ.setdefault("GST_PLUGIN_PATH", plugin_path)
    Gst.init(None)


def discover_file(path: str) -> dict[str, Any]:
    """Extract basic media info using gst-discoverer."""
    import gi

    gi.require_version("Gst", "1.0")
    gi.require_version("GstPbutils", "1.0")
    from gi.repository import Gst, GstPbutils

    Gst.init(None)
    discoverer = GstPbutils.Discoverer.new(5 * Gst.SECOND)
    uri = Path(path).resolve().as_uri()
    info = discoverer.discover_uri(uri)
    result: dict[str, Any] = {"duration_ms": info.get_duration() // Gst.MSECOND}

    for stream in info.get_stream_list():
        if isinstance(stream, GstPbutils.DiscovererVideoInfo):
            result["width"] = stream.get_width()
            result["height"] = stream.get_height()
            caps = stream.get_caps()
            if caps and caps.get_size() > 0:
                result["video_codec"] = caps.get_structure(0).get_name()
        elif isinstance(stream, GstPbutils.DiscovererAudioInfo):
            caps = stream.get_caps()
            if caps and caps.get_size() > 0:
                result["audio_codec"] = caps.get_structure(0).get_name()

    return result
