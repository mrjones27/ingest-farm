# Ingest Farm

Modular GStreamer broadcast ingest recording farm and MAM platform.

## Architecture

Ingest pipelines are composed from three swappable stages:

1. **SourceStage** — SRT, UDP/RTP, RTMP, HLS, file
2. **ProcessingStage** — passthrough (Phase 1), demux/encode (future)
3. **OutputStage** — MPEG-TS capture via `tsparse` + `multifilesink` (Phase 1)

Channel config selects a **pipeline profile**:


| Profile          | Behavior                                     |
| ---------------- | -------------------------------------------- |
| `ts_passthrough` | Raw MPEG-TS capture — no demux, no re-encode |


`transcode_remux` is not supported yet and is rejected by the API.

Encoders register via `EncoderRegistry`.

**Deployment note:** the control plane is validated for a **single ingest worker**. Multi-worker farm routing is future work.

## Prerequisites



### Linux (recommended)

```bash
sudo apt install \
  python3-gi python3-gi-cairo gir1.2-gstreamer-1.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly gstreamer1.0-libav
```



### Windows dev

Use WSL2 Ubuntu with the packages above. Native Windows GStreamer builds exist but Linux is the production target.

## Quick start (Docker control plane)

```bash
# API + worker + post-process + Postgres + Redis + web UI
docker compose up -d --build

# End-to-end: API start → UDP capture → stop → MAM asset
docker compose --profile gst run --rm gst python scripts/control_plane_test.py

# Ops console (served by the API)
open http://localhost:8080/

# OpenAPI docs
open http://localhost:8080/docs
```

Hot-reload UI against a running API:

```bash
cd web
npm install
npm run dev
```

Vite proxies `/api` and `/health` to `http://localhost:8080`.

## Capture smoke test (pipeline only)

```bash
docker compose --profile gst run --rm gst python scripts/capture_test.py --mode file
docker compose --profile gst run --rm gst python scripts/capture_test.py --mode udp --duration 8
```

Segments land under `data/capture-test/{file,udp}/`.

## Local Python (optional)

```bash
docker compose up -d postgres redis
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e .
cp .env.example .env
# GStreamer/SRT knobs live in .env (GST_SRT_LATENCY_MS, queues, thumbs, HLS proxy).
# Schema is applied via Alembic on API/worker/postprocess startup (upgrade head)

python scripts/run_api.py
python scripts/run_worker.py       # needs GStreamer
python scripts/run_postprocess.py  # needs GStreamer for proxy/thumbnail

# Ops console with hot reload (separate terminal)
cd web && npm install && npm run dev
```



## Create a channel and start recording

```bash
curl -s -X POST http://localhost:8080/api/channels \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "udp-live",
    "source": {
      "protocol": "udp",
      "uri": "udp://0.0.0.0:5000",
      "config": {"caps": "video/mpegts"}
    },
    "pipeline": {
      "profile": "ts_passthrough",
      "segment_duration_sec": 3600
    },
    "output": {
      "container": "mpegts"
    }
  }'

# Start / stop
curl -X POST http://localhost:8080/api/channels/{id}/start
curl -X POST http://localhost:8080/api/channels/{id}/stop

# Browse assets / workers / media
curl http://localhost:8080/api/assets
curl "http://localhost:8080/api/assets?channel_id={id}"
curl http://localhost:8080/api/assets/{asset_id}
curl http://localhost:8080/api/assets/{asset_id}/thumbnail
curl http://localhost:8080/api/assets/{asset_id}/proxy/playlist.m3u8
curl http://localhost:8080/api/workers
curl http://localhost:8080/health
```

Supported ingest protocols: `srt`, `udp` (mpegts / `rtp-h264` / `rtp-mp2t`), `rtmp`, `hls`, `file`.

## Live connect / record (SRT)

Connect starts the listener without writing segments; Record opens the gated capture branch.

```bash
curl -X POST http://localhost:8080/api/channels/{id}/connect
curl -X POST http://localhost:8080/api/channels/{id}/record/start
curl -X POST http://localhost:8080/api/channels/{id}/record/stop
curl -X POST http://localhost:8080/api/channels/{id}/disconnect
```



### SRT listener (farm listens, OBS/ffmpeg calls in)

Publish the listen port on the worker (see `docker-compose.yml`, e.g. `5001:5001/udp`). Channel URI example:

`srt://0.0.0.0:5001?mode=listener`

Caller (OBS / ffmpeg / gst): `srt://127.0.0.1:5001` (or `srt://worker:5001` from another compose service).

**Implementation note (gst 1.24 / libsrt):** an in-process `compose_live` SRT listener aborts the worker when a caller connects. SRT sessions therefore run in a **child process** (`ingest_farm.worker.srt_session_proc`) built with `ElementFactory` (no URI interpolation into `parse_launch`):

`srtsrc` (`keep-listening=true`) → leaky queue → tee → JPEG preview branch (or fakesink fallback) + valve-gated record branch.

Do not call `srtsrc.get_property("stats")` or `bus.add_signal_watch()` on the live SRT path — both have aborted libsrt on this stack. Receiving state is inferred from buffer probes in the child.

SRT **caller** mode (farm dials out to an OBS/ffmpeg listener) still uses the in-process live session and remains the simpler fallback if needed.

### ETR 290 transport-stream health (SRT)

After SRT is connected **and** MPEG-TS is flowing, the SRT child process taps the live tee into a localhost UDP feed for [TSDuck](https://tsduck.io/) `tsp -I ip <port> -P analyze --json -P influx --tr-101-290`. `tsp` is started only once the pad probe reports receiving, and stopped on disconnect. Counters and the MPEG-TS PID map are published on the channel page under **ETR 290 (transport stream)** via the existing Redis stats path (`stats.etr290`). P1/P2 values and TSDuck `error_count` are **session totals** since connect or the last Reset; packet count, bitrate and the PID map are from TSDuck's last analysis interval (the PID map is cumulative for the session). Stale snapshots (older than a few report intervals) are not shown as current.

This is **not** SRT socket health — a connected SRT session can still carry invalid TS. Priority 1 and 2 logical errors are shown (PCR repetition included). Contribution encoders often trip PCR repetition even when the stream is usable for ingest. The monitor branch uses its own leaky queue; if that queue overruns, the UI reports tap drops so local loss is not mistaken for a source continuity error.

Disable with `ETR290_ENABLED=false` or set `ETR290_INTERVAL_SEC` (default 2). Requires `tsp` in the worker image (`Dockerfile.gst` pins the TSDuck `.deb`).

## Project layout

```
ingest_farm/
  pipeline/
    builder.py              # Composes stages from channel profile
    stages/
      source/               # SRT, UDP/RTP, RTMP, HLS, file
      processing/           # Passthrough (Phase 1)
      output/               # TS capture + live tee
    encoders/
      registry.py           # Swappable encoder plugins
  worker/                   # GStreamer recorder (+ SRT child session)
    srt_session_proc.py     # Isolated SRT listener process
    etr290_sidecar.py       # TSDuck tsp TR 101 290 monitor sidecar
  orchestrator/             # Redis job scheduler
  postprocess/              # HLS proxy + thumbnail + catalog
  api/                      # FastAPI control plane + media serving + SPA
web/                        # Vite + React ops console
scripts/
  capture_test.py           # Pipeline-only smoke test
  control_plane_test.py     # API → worker → asset E2E test
```



## Roadmap

- [ ] `transcode_remux` profile — demux, encoder registry, remux (MKV/MXF)
- [ ] External SDK adapters
- [ ] Audio track in HLS proxy
- [ ] Multi-worker farm job routing
- [ ] Full libsrt stats on the SRT child path
- [ ] Archive tiers / approval workflow