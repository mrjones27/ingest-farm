from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from ingest_farm.common.gst_utils import init_gstreamer
from ingest_farm.pipeline.builder import PipelineBuilder
from ingest_farm.schemas import ChannelConfig, new_id

logger = logging.getLogger(__name__)


class PipelineRecorder:
    """Runs a composed GStreamer pipeline with bus monitoring."""

    def __init__(self, on_segment: Callable[[Path], None] | None = None) -> None:
        init_gstreamer()
        self._builder = PipelineBuilder()
        self._pipeline = None
        self._loop = None
        self._thread: threading.Thread | None = None
        self._on_segment = on_segment
        self._session_id = new_id()
        self._output_dir: Path | None = None
        self._segment_count = 0
        self._stopping = False
        self._channel_name = ""

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def output_dir(self) -> Path | None:
        return self._output_dir

    @property
    def segment_count(self) -> int:
        return self._segment_count

    @property
    def is_running(self) -> bool:
        return self._pipeline is not None

    def start(self, config: ChannelConfig, output_dir: Path) -> None:
        from gi.repository import GLib, Gst

        if self._pipeline is not None:
            raise RuntimeError("Recorder already running")

        self._channel_name = config.name
        self._output_dir = output_dir
        self._stopping = False
        ctx: dict[str, Any] = {"output_dir": output_dir}
        self._pipeline = self._builder.compose(config, ctx)

        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to start GStreamer pipeline")

        self._loop = GLib.MainLoop()
        self._thread = threading.Thread(
            target=self._loop.run, daemon=True, name=f"gst-{config.name}"
        )
        self._thread.start()
        logger.info("Pipeline started for channel %s → %s", config.name, output_dir)

    def stop(self) -> None:
        from gi.repository import Gst

        if self._stopping:
            return
        self._stopping = True

        if self._pipeline is None:
            return

        pipeline = self._pipeline
        loop = self._loop
        thread = self._thread
        same_thread = thread is not None and thread is threading.current_thread()

        # Clear first so concurrent stop/EOS calls are no-ops.
        self._pipeline = None
        self._loop = None
        self._thread = None

        pipeline.set_state(Gst.State.NULL)
        if loop is not None and loop.is_running():
            loop.quit()

        # Bus callbacks run on the GLib thread — never join ourselves.
        if thread is not None and not same_thread:
            thread.join(timeout=10)

        if self._output_dir is not None:
            self._segment_count = len(list(self._output_dir.glob("*.ts")))

        logger.info("Pipeline stopped")

    def _request_stop_from_bus(self, reason: str) -> None:
        """Schedule stop on the GLib idle queue — never tear down mid bus-handler."""
        from gi.repository import GLib

        def _idle_stop() -> bool:
            try:
                self.stop()
            except Exception:
                logger.exception("Deferred pipeline stop failed (%s)", reason)
            return False

        GLib.idle_add(_idle_stop)

    def _on_bus_message(self, bus: Any, message: Any) -> None:
        from gi.repository import Gst

        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error("Pipeline error: %s (%s)", err, debug)
            self._request_stop_from_bus("error")
        elif message.type == Gst.MessageType.EOS:
            logger.info("Pipeline EOS")
            self._request_stop_from_bus("eos")
