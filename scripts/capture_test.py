#!/usr/bin/env python3
"""End-to-end MPEG-TS capture smoke test using GStreamer.

Modes:
  file  — generate a short TS clip, capture via file source (default)
  udp   — listen on UDP and capture a live test pattern sender
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("capture_test")


def require_gstreamer() -> None:
    from ingest_farm.common.gst_utils import init_gstreamer

    init_gstreamer()
    from gi.repository import Gst

    for name in ("filesrc", "udpsrc", "tsparse", "multifilesink", "mpegtsmux", "videotestsrc"):
        if Gst.ElementFactory.find(name) is None:
            raise RuntimeError(f"Required GStreamer element missing: {name}")


def generate_sample_ts(path: Path, seconds: int = 5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "gst-launch-1.0",
        "-e",
        "videotestsrc",
        "is-live=false",
        f"num-buffers={seconds * 25}",
        "!",
        "video/x-raw,width=640,height=360,framerate=25/1",
        "!",
        "x264enc",
        "tune=zerolatency",
        "bitrate=800",
        "!",
        "h264parse",
        "!",
        "mpegtsmux",
        "!",
        "filesink",
        f"location={path}",
    ]
    logger.info("Generating sample TS: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    size = path.stat().st_size
    if size < 1024:
        raise RuntimeError(f"Sample TS too small ({size} bytes): {path}")
    logger.info("Sample TS ready: %s (%d bytes)", path, size)


def run_file_capture(output_dir: Path, sample: Path, segment_sec: int) -> Path:
    from ingest_farm.schemas import (
        ChannelConfig,
        OutputConfig,
        PipelineConfig,
        PipelineProfile,
        SourceConfig,
        SourceProtocol,
    )
    from ingest_farm.worker.recorder import PipelineRecorder

    output_dir.mkdir(parents=True, exist_ok=True)
    config = ChannelConfig(
        name="capture-file-test",
        source=SourceConfig(protocol=SourceProtocol.FILE, uri=str(sample)),
        pipeline=PipelineConfig(
            profile=PipelineProfile.TS_PASSTHROUGH,
            segment_duration_sec=segment_sec,
        ),
        output=OutputConfig(container="mpegts"),
    )

    recorder = PipelineRecorder()
    logger.info("Starting file capture → %s", output_dir)
    recorder.start(config, output_dir)

    deadline = time.time() + 30
    while recorder.is_running and time.time() < deadline:
        time.sleep(0.25)

    if recorder.is_running:
        recorder.stop()

    return output_dir


def run_udp_capture(output_dir: Path, port: int, duration_sec: int, segment_sec: int) -> Path:
    from ingest_farm.schemas import (
        ChannelConfig,
        OutputConfig,
        PipelineConfig,
        PipelineProfile,
        SourceConfig,
        SourceProtocol,
    )
    from ingest_farm.worker.recorder import PipelineRecorder

    output_dir.mkdir(parents=True, exist_ok=True)
    config = ChannelConfig(
        name="capture-udp-test",
        source=SourceConfig(
            protocol=SourceProtocol.UDP,
            uri=f"udp://0.0.0.0:{port}",
            config={"caps": "video/mpegts"},
        ),
        pipeline=PipelineConfig(
            profile=PipelineProfile.TS_PASSTHROUGH,
            segment_duration_sec=segment_sec,
        ),
        output=OutputConfig(container="mpegts"),
    )

    recorder = PipelineRecorder()
    logger.info("Starting UDP capture on port %s → %s", port, output_dir)
    recorder.start(config, output_dir)
    time.sleep(1)

    sender = [
        "gst-launch-1.0",
        "-e",
        "videotestsrc",
        "is-live=true",
        "!",
        "video/x-raw,width=640,height=360,framerate=25/1",
        "!",
        "x264enc",
        "tune=zerolatency",
        "bitrate=800",
        "!",
        "h264parse",
        "!",
        "mpegtsmux",
        "!",
        "udpsink",
        "host=127.0.0.1",
        f"port={port}",
        "sync=false",
    ]
    logger.info("Starting UDP sender for %ss", duration_sec)
    proc = subprocess.Popen(sender)
    try:
        time.sleep(duration_sec)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        recorder.stop()

    return output_dir


def validate_capture(output_dir: Path) -> list[Path]:
    segments = sorted(output_dir.glob("*.ts"))
    if not segments:
        raise RuntimeError(f"No .ts segments written under {output_dir}")

    total = 0
    for segment in segments:
        size = segment.stat().st_size
        total += size
        logger.info("Segment %s — %d bytes", segment.name, size)
        if size < 188:
            raise RuntimeError(f"Segment too small to be MPEG-TS: {segment}")

        # MPEG-TS sync byte check on first packet
        with segment.open("rb") as fh:
            sync = fh.read(1)
        if sync != b"\x47":
            raise RuntimeError(f"Missing MPEG-TS sync byte 0x47 in {segment}")

    logger.info("Capture OK — %d segment(s), %d bytes total", len(segments), total)
    return segments


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MPEG-TS passthrough capture smoke test")
    parser.add_argument("--mode", choices=("file", "udp"), default="file")
    parser.add_argument("--output", type=Path, default=Path("/data/capture-test"))
    parser.add_argument("--sample", type=Path, default=Path("/data/fixtures/sample.ts"))
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--duration", type=int, default=8, help="UDP send duration seconds")
    parser.add_argument("--segment-sec", type=int, default=3600)
    args = parser.parse_args(argv)

    require_gstreamer()
    run_dir = args.output / args.mode
    if run_dir.exists():
        for old in run_dir.glob("*.ts"):
            old.unlink()
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "file":
        if not args.sample.exists():
            generate_sample_ts(args.sample, seconds=5)
        run_file_capture(run_dir, args.sample, args.segment_sec)
    else:
        run_udp_capture(run_dir, args.port, args.duration, args.segment_sec)

    validate_capture(run_dir)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logger.exception("Capture test failed")
        raise SystemExit(1)
