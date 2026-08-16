from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable

from ingest_farm.common.gst_stats import gst_structure_to_dict, normalize_srt_stats
from ingest_farm.common.gst_utils import init_gstreamer
from ingest_farm.pipeline.builder import PipelineBuilder
from ingest_farm.schemas import ChannelConfig, new_id

logger = logging.getLogger(__name__)


class ChannelSession:
    """Live channel session: connect (source + preview) separate from recording."""

    def __init__(self) -> None:
        self._builder = None
        self._pipeline = None
        self._loop = None
        self._thread: threading.Thread | None = None
        self._session_id = new_id()
        self._preview_dir: Path | None = None
        self._output_dir: Path | None = None
        self._segment_count = 0
        self._stopping = False
        self._recording = False
        self._channel_name = ""
        self._valve = None
        self._record_sink = None
        self._thumb_path: Path | None = None
        self._source = None
        self._source_kind: str | None = None
        self._stats: dict[str, Any] = {}
        self._stats_lock = threading.Lock()
        self._stats_timeout_id: int | None = None
        self._record_started_at: float | None = None
        self._thumb_log_counter = 0
        self._srt_has_data = False

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def output_dir(self) -> Path | None:
        return self._output_dir

    @property
    def preview_dir(self) -> Path | None:
        return self._preview_dir

    @property
    def thumb_path(self) -> Path | None:
        return self._thumb_path

    @property
    def segment_count(self) -> int:
        return self._segment_count

    @property
    def is_connected(self) -> bool:
        return self._pipeline is not None

    @property
    def is_recording(self) -> bool:
        return self._recording

    def get_stats(self) -> dict[str, Any]:
        with self._stats_lock:
            return dict(self._stats)

    def connect(self, config: ChannelConfig, preview_dir: Path) -> None:
        if self._pipeline is not None:
            raise RuntimeError("Session already connected")

        self._channel_name = config.name
        self._preview_dir = preview_dir
        self._stopping = False
        self._recording = False
        with self._stats_lock:
            self._stats = {}
        # Must run on the process main / GLib thread. A background gst-*
        # thread accepted no SRT buffers (or SIGABRT); compose_live on main
        # got 176. The worker iterates a process-wide MainLoop on main.
        self._srt_has_data = False
        init_gstreamer()
        from gi.repository import GLib, Gst

        if self._builder is None:
            self._builder = PipelineBuilder()
        ctx: dict[str, Any] = {"preview_dir": preview_dir}
        self._pipeline = self._builder.compose_live(config, ctx)
        self._valve = ctx.get("record_valve")
        self._record_sink = ctx.get("record_sink")
        self._thumb_path = ctx.get("thumb_path")
        self._source = ctx.get("source_element")
        self._source_kind = ctx.get("source_kind")
        if self._source is not None:
            srcpad = self._source.get_static_pad("src")
            if srcpad is not None:

                def _on_first_buffer(pad: Any, info: Any) -> Any:
                    self._srt_has_data = True
                    # #region agent log
                    from ingest_farm.common.agent_debug import agent_log

                    agent_log(
                        "B",
                        "recorder.py:connect",
                        "srtsrc first buffer",
                        {
                            "channel": self._channel_name,
                            "pad": pad.get_name() if pad else None,
                        },
                        run_id="obs-caller",
                    )
                    # #endregion
                    return Gst.PadProbeReturn.REMOVE

                srcpad.add_probe(Gst.PadProbeType.BUFFER, _on_first_buffer)
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_bus_message)
        src_props = {}
        if self._source is not None:
            for name in (
                "uri",
                "latency",
                "wait-for-connection",
                "keep-listening",
                "authentication",
                "localport",
                "mode",
            ):
                if self._source.find_property(name):
                    try:
                        src_props[name] = self._source.get_property(name)
                    except Exception as exc:
                        src_props[name] = f"err:{exc}"
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._pipeline = None
            raise RuntimeError("Failed to start GStreamer pipeline")
        # #region agent log
        from ingest_farm.common.agent_debug import agent_log

        agent_log(
            "H",
            "recorder.py:connect",
            "pipeline PLAYING on glib thread",
            {
                "channel": config.name,
                "source_kind": self._source_kind,
                "uri": config.source.uri,
                "state_change": int(ret),
                "thread": threading.current_thread().name,
                "src_props": src_props,
            },
            run_id="obs-caller",
        )
        # #endregion
        self._loop = None
        self._thread = None
        self._stats_timeout_id = GLib.timeout_add_seconds(1, self._poll_stats)
        logger.info("Channel %s connected → preview %s", config.name, preview_dir)

    def start_recording(self, output_dir: Path) -> None:
        if self._pipeline is None or self._valve is None or self._record_sink is None:
            raise RuntimeError("Not connected")
        if self._recording:
            raise RuntimeError("Already recording")

        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir = output_dir
        self._segment_count = 0
        location = str(output_dir / "segment_%05d.ts")
        self._record_sink.set_property("location", location)
        self._valve.set_property("drop", False)
        self._recording = True
        import time as _time

        self._record_started_at = _time.time()
        # #region agent log
        from ingest_farm.common.agent_debug import agent_log

        agent_log(
            "A",
            "recorder.py:start_recording",
            "valve opened",
            {"channel": self._channel_name, "output_dir": str(output_dir)},
        )
        # #endregion
        logger.info("Channel %s recording → %s", self._channel_name, output_dir)

    def stop_recording(self) -> None:
        if not self._recording:
            return
        if self._valve is not None:
            self._valve.set_property("drop", True)
        self._recording = False
        import time as _time

        wall_sec = None
        if self._record_started_at is not None:
            wall_sec = _time.time() - self._record_started_at
        self._record_started_at = None
        sizes: list[int] = []
        if self._output_dir is not None:
            segs = list(self._output_dir.glob("*.ts"))
            self._segment_count = len(segs)
            sizes = [p.stat().st_size for p in segs]
        # #region agent log
        from ingest_farm.common.agent_debug import agent_log

        agent_log(
            "A",
            "recorder.py:stop_recording",
            "valve closed",
            {
                "channel": self._channel_name,
                "wall_sec": wall_sec,
                "segment_count": self._segment_count,
                "segment_bytes": sizes,
                "total_bytes": sum(sizes),
            },
            run_id="post-fix",
        )
        # #endregion
        logger.info(
            "Channel %s stopped recording (%s segments)",
            self._channel_name,
            self._segment_count,
        )

    def disconnect(self) -> None:
        if self._recording:
            self.stop_recording()
        self._teardown()

    def _poll_stats(self) -> bool:
        if self._stopping or self._pipeline is None:
            return False
        # #region agent log
        self._thumb_log_counter += 1
        if self._thumb_log_counter % 3 == 0:
            from ingest_farm.common.agent_debug import agent_log

            thumb_info: dict[str, Any] = {"exists": False}
            if self._thumb_path is not None:
                try:
                    st = self._thumb_path.stat()
                    thumb_info = {
                        "exists": True,
                        "bytes": st.st_size,
                        "mtime": st.st_mtime,
                        "age_sec": __import__("time").time() - st.st_mtime,
                    }
                    if st.st_size >= 100:
                        import shutil

                        stable = self._thumb_path.with_name("thumb.ok.jpg")
                        shutil.copyfile(self._thumb_path, stable)
                except FileNotFoundError:
                    thumb_info = {"exists": False}
            qlevels: dict[str, Any] = {}
            if self._pipeline is not None:
                for name in ("preview-queue", "record-queue"):
                    el = self._pipeline.get_by_name(name)
                    if el is None:
                        continue
                    try:
                        qlevels[name] = {
                            "current-level-time": int(el.get_property("current-level-time")),
                            "current-level-buffers": int(el.get_property("current-level-buffers")),
                            "current-level-bytes": int(el.get_property("current-level-bytes")),
                        }
                    except Exception:
                        pass
            agent_log(
                "A",
                "recorder.py:_poll_stats",
                "thumb+queue snapshot",
                {
                    "channel": self._channel_name,
                    "recording": self._recording,
                    "thumb": thumb_info,
                    "queues": qlevels,
                    "srt": {
                        "rtt-ms": self._stats.get("rtt-ms"),
                        "lost": self._stats.get("packets-received-lost"),
                        "recv-mbps": self._stats.get("receive-rate-mbps"),
                        "latency-ms": self._stats.get("negotiated-latency-ms"),
                        "caller_count": self._stats.get("caller_count"),
                        "packets-received": self._stats.get("packets-received"),
                        "bytes-received": self._stats.get("bytes-received")
                        or self._stats.get("bytes-received-total"),
                    }
                    if self._stats
                    else None,
                },
                run_id="post-fix",
            )
        # #endregion
        if self._source is None or self._source_kind != "srt":
            return True
        if not self._srt_has_data:
            # #region agent log
            if self._thumb_log_counter <= 20 or self._thumb_log_counter % 5 == 0:
                from ingest_farm.common.agent_debug import agent_log

                agent_log(
                    "F",
                    "recorder.py:_poll_stats",
                    "waiting for srt data (stats poll skipped)",
                    {"channel": self._channel_name, "n": self._thumb_log_counter},
                    run_id="obs-caller",
                )
            # #endregion
            return True
        if not self._source.find_property("stats"):
            return True
        try:
            structure = self._source.get_property("stats")
            raw = gst_structure_to_dict(structure)
            normalized = normalize_srt_stats(raw)
            normalized["protocol"] = "srt"
            with self._stats_lock:
                self._stats = normalized
            # #region agent log
            if self._thumb_log_counter <= 20 or self._thumb_log_counter % 5 == 0:
                from ingest_farm.common.agent_debug import agent_log

                agent_log(
                    "B",
                    "recorder.py:_poll_stats",
                    "srt handshake snapshot",
                    {
                        "channel": self._channel_name,
                        "n": self._thumb_log_counter,
                        "caller_count": normalized.get("caller_count"),
                        "packets-received": normalized.get("packets-received"),
                        "bytes-received": normalized.get("bytes-received")
                        or normalized.get("bytes-received-total"),
                        "recv-mbps": normalized.get("receive-rate-mbps"),
                        "latency-ms": normalized.get("negotiated-latency-ms"),
                        "rtt-ms": normalized.get("rtt-ms"),
                    },
                    run_id="obs-caller",
                )
            # #endregion
        except Exception:
            logger.debug("Failed to read SRT stats for %s", self._channel_name, exc_info=True)
        return True

    def _teardown(self) -> None:
        from gi.repository import GLib, Gst

        if self._stopping:
            return
        self._stopping = True

        if self._stats_timeout_id is not None:
            GLib.source_remove(self._stats_timeout_id)
            self._stats_timeout_id = None

        if self._pipeline is None:
            with self._stats_lock:
                self._stats = {}
            return

        pipeline = self._pipeline
        loop = self._loop
        thread = self._thread
        same_thread = thread is not None and thread is threading.current_thread()

        self._pipeline = None
        self._loop = None
        self._thread = None
        self._valve = None
        self._record_sink = None
        self._source = None
        self._source_kind = None
        self._recording = False
        with self._stats_lock:
            self._stats = {}

        pipeline.set_state(Gst.State.NULL)
        if loop is not None and loop.is_running():
            loop.quit()
        if thread is not None and not same_thread:
            thread.join(timeout=10)
        logger.info("Channel %s disconnected", self._channel_name)

    def _request_teardown_from_bus(self, reason: str) -> None:
        from gi.repository import GLib

        def _idle() -> bool:
            try:
                self._teardown()
            except Exception:
                logger.exception("Deferred disconnect failed (%s)", reason)
            return False

        GLib.idle_add(_idle)

    def _on_bus_message(self, bus: Any, message: Any) -> None:
        from gi.repository import Gst

        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            src = message.src.get_name() if message.src else "?"
            # #region agent log
            from ingest_farm.common.agent_debug import agent_log

            agent_log(
                "C",
                "recorder.py:_on_bus_message",
                "GST ERROR",
                {"src": src, "err": str(err), "debug": str(debug)},
            )
            # #endregion
            logger.error("Pipeline error: %s (%s)", err, debug)
            self._request_teardown_from_bus("error")
        elif message.type == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            src = message.src.get_name() if message.src else "?"
            # #region agent log
            from ingest_farm.common.agent_debug import agent_log

            agent_log(
                "C",
                "recorder.py:_on_bus_message",
                "GST WARNING",
                {"src": src, "err": str(err), "debug": str(debug)},
            )
            # #endregion
        elif message.type == Gst.MessageType.EOS:
            logger.info("Pipeline EOS")
            self._request_teardown_from_bus("eos")


# Back-compat alias used by capture_test / older call sites.
class PipelineRecorder:
    """Legacy always-record helper wrapping ChannelSession + compose()."""

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

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def output_dir(self) -> Path | None:
        return self._output_dir

    @property
    def segment_count(self) -> int:
        return self._segment_count

    def start(self, config: ChannelConfig, output_dir: Path) -> None:
        from gi.repository import GLib, Gst

        if self._pipeline is not None:
            raise RuntimeError("Recorder already running")

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
        self._thread = threading.Thread(target=self._loop.run, daemon=True)
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
        self._pipeline = None
        self._loop = None
        self._thread = None

        pipeline.set_state(Gst.State.NULL)
        if loop is not None and loop.is_running():
            loop.quit()
        if thread is not None and not same_thread:
            thread.join(timeout=10)
        if self._output_dir is not None:
            self._segment_count = len(list(self._output_dir.glob("*.ts")))
        logger.info("Pipeline stopped")

    def _on_bus_message(self, bus: Any, message: Any) -> None:
        from gi.repository import GLib, Gst

        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            logger.error("Pipeline error: %s (%s)", err, debug)
            GLib.idle_add(lambda: (self.stop(), False)[1])
        elif message.type == Gst.MessageType.EOS:
            logger.info("Pipeline EOS")
            GLib.idle_add(lambda: (self.stop(), False)[1])
