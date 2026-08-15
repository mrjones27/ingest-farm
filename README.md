# Ingest Farm

Modular GStreamer broadcast ingest recording farm and MAM platform.

## Architecture

Ingest pipelines are composed from three swappable stages:

1. **SourceStage** — SRT, UDP/RTP, RTMP, HLS, file
2. **ProcessingStage** — passthrough (Phase 1), demux/encode (future)
3. **OutputStage** — MPEG-TS capture via `tsparse` + `multifilesink` (Phase 1)

Channel config selects a **pipeline profile**:

| Profile | Behavior |
|---------|----------|
| `ts_passthrough` | Raw MPEG-TS capture — no demux, no re-encode |
| `transcode_remux` | Future — demux essences, optional frame-rate convert, swappable encoders, remux |

Encoders register via `EncoderRegistry` (`gstreamer:x264enc`, `mainconcept:h264`, `insync`, etc.).

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
# API + worker + post-process + Postgres + Redis
docker compose up -d --build

# End-to-end: API start → UDP capture → stop → MAM asset
docker compose --profile gst run --rm gst python scripts/control_plane_test.py

# OpenAPI docs
open http://localhost:8080/docs
```

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

python scripts/run_api.py
python scripts/run_worker.py       # needs GStreamer
python scripts/run_postprocess.py  # needs GStreamer for proxy/thumbnail
```

## Create a channel and start recording

```bash
curl -s -X POST http://localhost:8080/channels \
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
curl -X POST http://localhost:8080/channels/{id}/start
curl -X POST http://localhost:8080/channels/{id}/stop

# Browse assets / workers / media
curl http://localhost:8080/assets
curl "http://localhost:8080/assets?channel_id={id}"
curl http://localhost:8080/assets/{asset_id}
curl http://localhost:8080/assets/{asset_id}/thumbnail
curl http://localhost:8080/assets/{asset_id}/proxy/playlist.m3u8
curl http://localhost:8080/workers
curl http://localhost:8080/health
```

Supported ingest protocols: `srt`, `udp` (mpegts / `rtp-h264` / `rtp-mp2t`), `rtmp`, `hls`, `file`.

## Project layout

```
ingest_farm/
  pipeline/
    builder.py              # Composes stages from channel profile
    stages/
      source/               # SRT, UDP/RTP, RTMP, HLS, file
      processing/           # Passthrough (Phase 1)
      output/               # TS capture
    encoders/
      registry.py           # Swappable encoder plugins
  worker/                   # GStreamer recorder
  orchestrator/             # Redis job scheduler
  postprocess/              # HLS proxy + thumbnail + catalog
  api/                      # FastAPI control plane + media serving
scripts/
  capture_test.py           # Pipeline-only smoke test
  control_plane_test.py     # API → worker → asset E2E test
```

## Roadmap

- [ ] `transcode_remux` profile — demux, encoder registry, remux (MKV/MXF)
- [ ] External SDK adapters — MainConcept, Insync
- [ ] Audio track in HLS proxy
- [ ] SRT control-plane E2E
- [ ] Archive tiers / approval workflow
