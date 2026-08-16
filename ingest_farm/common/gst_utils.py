from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def init_gstreamer(plugin_path: str = "") -> None:
    import os

    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    from ingest_farm.config import get_settings

    path = plugin_path or get_settings().gst_plugin_path
    if path:
        os.environ.setdefault("GST_PLUGIN_PATH", path)
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
    run_gst_pipeline(pipeline, timeout_sec=timeout_sec)


def run_gst_pipeline(pipeline: Any, timeout_sec: float = 120.0) -> None:
    """Run an already-built pipeline to EOS or error."""
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    bus = pipeline.get_bus()
    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        pipeline.set_state(Gst.State.NULL)
        raise RuntimeError("Failed to start pipeline")

    deadline = time.time() + timeout_sec
    try:
        while time.time() < deadline:
            msg = bus.timed_pop_filtered(
                500 * Gst.MSECOND,
                Gst.MessageType.ERROR | Gst.MessageType.EOS,
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

    from ingest_farm.config import get_settings

    Gst.init(None)
    timeout_ns = max(1, int(get_settings().gst_discoverer_timeout_sec)) * Gst.SECOND
    discoverer = GstPbutils.Discoverer.new(timeout_ns)
    uri = Path(path).resolve().as_uri()
    info = discoverer.discover_uri(uri)
    duration_ns = info.get_duration()
    result: dict[str, Any] = {}
    if duration_ns != Gst.CLOCK_TIME_NONE:
        result["duration_ms"] = duration_ns // Gst.MSECOND

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
