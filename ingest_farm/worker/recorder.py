from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ingest_farm.common.gst_utils import init_gstreamer
from ingest_farm.pipeline.builder import PipelineBuilder
from ingest_farm.schemas import ChannelConfig, new_id

logger = logging.getLogger(__name__)

# A source is only "receiving" while packets are actually arriving. A longer gap
# than this means the encoder went away, even if it never sent EOS. Matches the
# SRT child process so both paths report the same thing.
_RECEIVING_IDLE_SEC = 2.0


class ChannelSession:
    """Live channel session: connect (source + preview) separate from recording.

    SRT listeners run in a child process — in-worker srtsrc aborts libsrt when a
    caller connects (gst 1.24). Non-SRT sources still use an in-process pipeline.
    """

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
        self._record_idle_sink = None
        self._thumb_path: Path | None = None
        self._source = None
        self._source_kind: str | None = None
        self._stats: dict[str, Any] = {}
        self._stats_lock = threading.Lock()
        self._stats_timeout_id: int | None = None
        self._record_started_at: float | None = None
        self._thumb_log_counter = 0
        self._srt_has_data = False
        self._srt_proc: subprocess.Popen[str] | None = None
        self._srt_state_dir: Path | None = None
        self._preview_mode: str = "fakesink"
        self._segment_duration_sec: int = 3600
        self._bus = None
        self._error: str | None = None
        self._eos = False
        self._pipeline_state = "NULL"
        self._buffer_count = 0
        self._bytes_total = 0
        self._last_buffer_at = 0.0

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
        if self._srt_proc is not None:
            return self._srt_proc.poll() is None
        # A pipeline object says nothing about pipeline health; the bus does.
        if self._error is not None or self._eos:
            return False
        return self._pipeline is not None

    @property
    def end_reason(self) -> str | None:
        """Why the session stopped being usable, for operator-facing logs."""
        if self._error is not None:
            return self._error
        if self._eos:
            return "end of stream"
        return None

    @property
    def is_receiving(self) -> bool:
        """True only while buffers are actually arriving from the source."""
        if self._buffer_count == 0:
            return False
        return (time.monotonic() - self._last_buffer_at) < _RECEIVING_IDLE_SEC

    @property
    def is_recording(self) -> bool:
        return self._recording

    def get_stats(self) -> dict[str, Any]:
        if self._srt_proc is not None and self._srt_state_dir is not None:
            status_path = self._srt_state_dir / "status.json"
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
                with self._stats_lock:
                    self._stats = {
                        **data,
                        "available": True,
                        "protocol": "srt",
                        "receiving": bool(data.get("receiving")),
                        "preview": data.get("preview", "fakesink"),
                        "note": "srt child process",
                    }
            except (OSError, json.JSONDecodeError):
                pass
        elif self._pipeline is not None and self._srt_proc is None:
            # Promote thumb for in-process live tee.
            if self._thumb_path is not None and self._preview_dir is not None:
                try:
                    if self._thumb_path.is_file() and self._thumb_path.stat().st_size >= 100:
                        stable = self._preview_dir / "thumb.ok.jpg"
                        stable.write_bytes(self._thumb_path.read_bytes())
                except OSError:
                    pass
            with self._stats_lock:
                self._stats = self._live_stats()
        with self._stats_lock:
            return dict(self._stats)

    def _live_stats(self) -> dict[str, Any]:
        """Observed state of the in-process pipeline, not assumed state."""
        stats: dict[str, Any] = {
            "available": True,
            "protocol": str(self._source_kind or "unknown"),
            "receiving": self.is_receiving,
            "preview": self._preview_mode,
            "pipeline-state": self._pipeline_state,
            "bytes-received-total": self._bytes_total,
            "buffer-count": self._buffer_count,
        }
        if self._error is not None:
            stats["error"] = self._error
        if self._eos:
            stats["eos"] = True
        return stats

    def connect(self, config: ChannelConfig, preview_dir: Path) -> None:
        if self.is_connected:
            raise RuntimeError("Session already connected")

        self._channel_name = config.name
        self._preview_dir = preview_dir
        self._stopping = False
        self._recording = False
        with self._stats_lock:
            self._stats = {}
        self._error = None
        self._eos = False
        self._pipeline_state = "NULL"
        self._buffer_count = 0
        self._bytes_total = 0
        self._last_buffer_at = 0.0
        self._srt_has_data = False
        self._source_kind = config.source.protocol
        self._segment_duration_sec = config.pipeline.segment_duration_sec

        if config.source.protocol == "srt":
            self._connect_srt_child(config, preview_dir)
            return

        init_gstreamer()
        from gi.repository import Gst

        if self._builder is None:
            self._builder = PipelineBuilder()
        ctx: dict[str, Any] = {"preview_dir": preview_dir}
        self._pipeline = self._builder.compose_live(config, ctx)
        self._valve = ctx.get("record_valve")
        self._record_sink = ctx.get("record_sink")
        self._record_idle_sink = ctx.get("record_idle_sink")
        self._thumb_path = ctx.get("thumb_path")
        self._source = ctx.get("source_element")
        self._source_kind = ctx.get("source_kind") or config.source.protocol
        self._preview_mode = ctx.get("preview_mode", "jpeg")

        # Watch the bus before PLAYING: set_state returns ASYNC for live
        # sources, so failures surface here rather than in the return value.
        self._bus = self._pipeline.get_bus()
        self._bus.add_signal_watch()
        self._bus.connect("message", self._on_bus_message)
        self._attach_receive_probe(ctx.get("live_tee"), Gst)

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            self._teardown()
            raise RuntimeError("Failed to start GStreamer pipeline")
        self._loop = None
        self._thread = None
        self._stats_timeout_id = None
        with self._stats_lock:
            self._stats = self._live_stats()
        logger.info(
            "Channel %s connected → preview %s (set_state=%s)",
            config.name,
            preview_dir,
            ret.value_nick,
        )

    def _attach_receive_probe(self, tee: Any, gst: Any) -> None:
        if tee is None:
            logger.warning(
                "Channel %s has no live tee — cannot observe whether media arrives",
                self._channel_name,
            )
            return
        pad = tee.get_static_pad("sink")
        if pad is None:
            logger.warning("Channel %s live tee has no sink pad", self._channel_name)
            return
        # Runs on a streaming thread for every buffer, so keep the lookups out.
        probe_ok = gst.PadProbeReturn.OK

        def _on_buffer(_pad: Any, info: Any) -> Any:
            buf = info.get_buffer()
            self._buffer_count += 1
            self._bytes_total += buf.get_size() if buf is not None else 0
            self._last_buffer_at = time.monotonic()
            return probe_ok

        pad.add_probe(gst.PadProbeType.BUFFER, _on_buffer)

    def _on_bus_message(self, _bus: Any, message: Any) -> bool:
        from gi.repository import Gst

        src_name = message.src.get_name() if message.src is not None else "unknown"
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            self._error = f"{src_name}: {err.message}"
            logger.error(
                "Channel %s pipeline ERROR from %s: %s (debug=%s, state=%s)",
                self._channel_name,
                src_name,
                err.message,
                debug,
                self._pipeline_state,
            )
        elif message.type == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            logger.warning(
                "Channel %s pipeline WARNING from %s: %s (debug=%s)",
                self._channel_name,
                src_name,
                err.message,
                debug,
            )
        elif message.type == Gst.MessageType.EOS:
            self._eos = True
            logger.info("Channel %s pipeline EOS — source ended", self._channel_name)
        elif message.type == Gst.MessageType.STATE_CHANGED:
            if self._pipeline is not None and message.src is self._pipeline:
                _old, new, _pending = message.parse_state_changed()
                self._pipeline_state = Gst.Element.state_get_name(new)
        return True

    def _connect_srt_child(self, config: ChannelConfig, preview_dir: Path) -> None:
        state_dir = preview_dir / "_srt_session"
        if state_dir.exists():
            for p in state_dir.iterdir():
                if p.is_file():
                    p.unlink(missing_ok=True)
        else:
            state_dir.mkdir(parents=True, exist_ok=True)
        preview_dir.mkdir(parents=True, exist_ok=True)
        self._srt_state_dir = state_dir
        self._thumb_path = preview_dir / "thumb.jpg"

        log_path = state_dir / "child.log"
        log_f = open(log_path, "w", encoding="utf-8")  # noqa: SIM115 — owned by Popen
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ingest_farm.worker.srt_session_proc",
                config.source.uri,
                str(preview_dir),
                str(state_dir),
                str(config.pipeline.segment_duration_sec),
            ],
            stdout=log_f,
            stderr=subprocess.STDOUT,
            text=True,
        )
        log_f.close()
        ready = state_dir / "ready"
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                err = ""
                try:
                    err = log_path.read_text(encoding="utf-8")
                except OSError:
                    pass
                raise RuntimeError(f"SRT session process exited early: {err[:500]}")
            if ready.exists():
                break
            time.sleep(0.05)
        else:
            proc.kill()
            raise RuntimeError("SRT session process failed to become ready")

        self._srt_proc = proc
        self._pipeline = object()
        self._preview_mode = "jpeg"
        try:
            data = json.loads((state_dir / "status.json").read_text(encoding="utf-8"))
            self._preview_mode = str(data.get("preview") or "jpeg")
        except (OSError, json.JSONDecodeError):
            pass
        with self._stats_lock:
            self._stats = {
                "available": True,
                "protocol": "srt",
                "receiving": False,
                "preview": self._preview_mode,
                "note": "srt child process",
            }
        logger.info(
            "Channel %s SRT listener in child pid=%s → %s (preview=%s)",
            config.name,
            proc.pid,
            preview_dir,
            self._preview_mode,
        )

    def reset_etr290(self) -> None:
        if self._srt_proc is None or self._srt_state_dir is None:
            logger.info(
                "Channel %s ETR 290 reset skipped (not an SRT child session)",
                self._channel_name,
            )
            return
        self._srt_send_cmd("etr290_reset")

    def _srt_send_cmd(self, cmd: str) -> None:
        if self._srt_state_dir is None:
            raise RuntimeError("Not connected")
        path = self._srt_state_dir / "cmds"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(cmd.rstrip() + "\n")

    def _wait_srt_recording(self, want: bool, timeout_sec: float = 5.0) -> bool:
        if self._srt_state_dir is None:
            return False
        status_path = self._srt_state_dir / "status.json"
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
                if bool(data.get("recording")) is want:
                    return True
            except (OSError, json.JSONDecodeError):
                pass
            if self._srt_proc is not None and self._srt_proc.poll() is not None:
                return False
            time.sleep(0.05)
        return False

    def start_recording(self, output_dir: Path) -> None:
        if not self.is_connected:
            raise RuntimeError("Not connected")
        if self._recording:
            raise RuntimeError("Already recording")

        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir = output_dir
        self._segment_count = 0

        if self._srt_proc is not None:
            self._srt_send_cmd(f"record {output_dir}")
            if not self._wait_srt_recording(True):
                raise RuntimeError("SRT child did not acknowledge recording start")
            self._recording = True
            self._record_started_at = time.time()
            logger.info("Channel %s recording (child) → %s", self._channel_name, output_dir)
            return

        from gi.repository import Gst

        from ingest_farm.pipeline.stages.output.preview import relink_valve

        if self._valve is None or self._record_sink is None:
            raise RuntimeError("Not connected")

        location = str(output_dir / "segment_%05d.ts")
        self._record_sink.set_property("location", location)
        if not relink_valve(self._valve, self._record_sink, self._pipeline, Gst):
            raise RuntimeError("Failed to link valve → multifilesink")
        self._valve.set_property("drop", False)
        self._recording = True
        self._record_started_at = time.time()
        logger.info("Channel %s recording → %s", self._channel_name, output_dir)

    def stop_recording(self) -> None:
        if not self._recording:
            return

        if self._srt_proc is not None:
            try:
                self._srt_send_cmd("stop")
                if not self._wait_srt_recording(False, timeout_sec=3.0):
                    # The capture may be truncated or still writing; the caller
                    # will still mark the recording completed.
                    logger.warning(
                        "Channel %s SRT child did not acknowledge record stop",
                        self._channel_name,
                    )
            except Exception:
                logger.exception("Failed to send stop to SRT child")
        elif self._valve is not None:
            from gi.repository import Gst

            from ingest_farm.pipeline.stages.output.preview import relink_valve

            self._valve.set_property("drop", True)
            if self._record_idle_sink is not None:
                relink_valve(self._valve, self._record_idle_sink, self._pipeline, Gst)

        self._recording = False
        wall_sec = None
        if self._record_started_at is not None:
            wall_sec = time.time() - self._record_started_at
        self._record_started_at = None
        sizes: list[int] = []
        if self._output_dir is not None:
            segs = list(self._output_dir.glob("segment_*.ts"))
            self._segment_count = len(segs)
            sizes = [p.stat().st_size for p in segs]
        logger.info(
            "Channel %s stopped recording (%s segments, wall=%.1fs, bytes=%s)",
            self._channel_name,
            self._segment_count,
            wall_sec or 0.0,
            sum(sizes),
        )

    def disconnect(self) -> None:
        if self._recording:
            self.stop_recording()
        self._teardown()

    def _teardown(self) -> None:
        if self._stopping:
            return
        self._stopping = True

        if self._srt_proc is not None:
            try:
                self._srt_send_cmd("quit")
            except Exception:
                pass
            try:
                self._srt_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self._srt_proc.kill()
                self._srt_proc.wait(timeout=3)
            self._srt_proc = None
            self._srt_state_dir = None
            self._pipeline = None
            self._source_kind = None
            self._recording = False
            with self._stats_lock:
                self._stats = {}
            logger.info("Channel %s disconnected (SRT child)", self._channel_name)
            return

        from gi.repository import GLib, Gst

        if self._stats_timeout_id is not None:
            GLib.source_remove(self._stats_timeout_id)
            self._stats_timeout_id = None

        if self._bus is not None:
            # Leaving the watch attached keeps a source on the default main
            # context after the pipeline is gone.
            self._bus.remove_signal_watch()
            self._bus = None

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
            self._segment_count = len(list(self._output_dir.glob("segment_*.ts")))
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
