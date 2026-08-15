#!/usr/bin/env python3
"""Control-plane end-to-end smoke test.

Flow: create UDP channel → start via API → send live MPEG-TS → stop → wait for MAM asset.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
import uuid
from typing import Any

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("control_plane_test")


def wait_for_ready(client: httpx.Client, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    last_error = "not started"
    while time.time() < deadline:
        try:
            response = client.get("/health")
            body = response.json()
            if response.status_code == 200 and body.get("status") == "ok":
                logger.info("API ready: %s", body)
                return
            last_error = str(body)
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"API not ready after {timeout}s: {last_error}")


def wait_for_workers(client: httpx.Client, timeout: float = 60.0) -> list[dict[str, Any]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        workers = client.get("/api/workers").json()
        if workers:
            logger.info("Workers online: %s", [w.get("hostname") for w in workers])
            return workers
        time.sleep(1)
    raise RuntimeError("No ingest workers registered")


def wait_channel_status(
    client: httpx.Client,
    channel_id: str,
    wanted: set[str],
    timeout: float = 60.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = client.get(f"/api/channels/{channel_id}").json()
        if last.get("status") in wanted:
            return last
        time.sleep(0.5)
    raise RuntimeError(f"Channel {channel_id} never reached {wanted}; last={last}")


def wait_for_asset(
    client: httpx.Client,
    channel_name: str,
    timeout: float = 60.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        assets = client.get("/api/assets").json()
        for asset in assets:
            if channel_name in (asset.get("title") or ""):
                return asset
        time.sleep(1)
    raise RuntimeError(f"No asset cataloged for channel {channel_name}")


def send_udp_ts(host: str, port: int, duration_sec: int) -> None:
    cmd = [
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
        f"host={host}",
        f"port={port}",
        "sync=false",
    ]
    logger.info("Sending UDP MPEG-TS to %s:%s for %ss", host, port, duration_sec)
    proc = subprocess.Popen(cmd)
    try:
        time.sleep(duration_sec)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Control-plane E2E smoke test")
    parser.add_argument("--api", default="http://api:8080")
    parser.add_argument("--udp-host", default="worker", help="Ingest worker hostname for UDP")
    parser.add_argument("--udp-port", type=int, default=5000)
    parser.add_argument("--duration", type=int, default=8)
    args = parser.parse_args(argv)

    channel_name = f"e2e-udp-{uuid.uuid4().hex[:8]}"

    with httpx.Client(base_url=args.api, timeout=30.0) as client:
        wait_for_ready(client)
        wait_for_workers(client)

        created = client.post(
            "/api/channels",
            json={
                "name": channel_name,
                "source": {
                    "protocol": "udp",
                    "uri": f"udp://0.0.0.0:{args.udp_port}",
                    "config": {"caps": "video/mpegts"},
                },
                "pipeline": {"profile": "ts_passthrough", "segment_duration_sec": 3600},
                "output": {"container": "mpegts"},
            },
        )
        created.raise_for_status()
        channel = created.json()
        channel_id = channel["id"]
        logger.info("Created channel %s (%s)", channel_name, channel_id)

        started = client.post(f"/api/channels/{channel_id}/start")
        started.raise_for_status()
        wait_channel_status(client, channel_id, {"recording"})
        logger.info("Channel is recording")

        send_udp_ts(args.udp_host, args.udp_port, args.duration)

        stopped = client.post(f"/api/channels/{channel_id}/stop")
        stopped.raise_for_status()
        wait_channel_status(client, channel_id, {"idle", "stopping"})
        # Worker may briefly report stopping before idle.
        wait_channel_status(client, channel_id, {"idle"}, timeout=30)
        logger.info("Channel stopped")

        asset = wait_for_asset(client, channel_name)
        logger.info(
            "Control plane OK — asset=%s master=%s segments=%s",
            asset["id"],
            asset["master_path"],
            asset.get("metadata", {}).get("segment_count"),
        )
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        logger.exception("Control-plane test failed")
        raise SystemExit(1)
