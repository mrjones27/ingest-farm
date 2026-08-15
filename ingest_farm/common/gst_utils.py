from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def init_gstreamer(plugin_path: str = "") -> None:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    if plugin_path:
        import os

        os.environ.setdefault("GST_PLUGIN_PATH", plugin_path)
    Gst.init(None)


def run_pipeline_string(description: str, timeout_sec: float = 120.0) -> None:
    """Run a gst-launch-style pipeline description to EOS or error."""
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    init_gstreamer()
    pipeline = Gst.parse_launch(description)
    if pipeline is None:
        raise RuntimeError(f"Failed to parse pipeline: {description}")

    bus = pipeline.get_bus()
    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        pipeline.set_state(Gst.State.NULL)
        raise RuntimeError(f"Failed to start pipeline: {description}")

    deadline = time.time() + timeout_sec
    try:
        while time.time() < deadline:
            msg = bus.timed_pop_filtered(
                500 * Gst.MSECOND,
                Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.ELEMENT,
            )
            if msg is None:
                continue
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                raise RuntimeError(f"Pipeline error: {err} ({debug})")
            if msg.type == Gst.MessageType.EOS:
                return
    finally:
        pipeline.set_state(Gst.State.NULL)

    raise TimeoutError(f"Pipeline timed out after {timeout_sec}s")


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
