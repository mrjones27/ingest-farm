# Ingest Farm

Modular GStreamer broadcast ingest recording farm and MAM platform.

## Architecture

Ingest pipelines are composed from three swappable stages:

1. **SourceStage** — SRT, UDP, file (RTMP/HLS later)
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

## Capture smoke test (Docker + GStreamer)

No native GStreamer on Windows required — runs inside a Ubuntu image:

```bash
# File-source passthrough (generate sample TS → capture segments)
docker compose --profile gst run --rm gst python scripts/capture_test.py --mode file

# Live UDP pattern → capture
docker compose --profile gst run --rm gst python scripts/capture_test.py --mode udp --duration 8
```

Segments land under `data/capture-test/{file,udp}/`.

## Quick start

```bash
# Infrastructure
docker compose up -d

# Python env
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e .

cp .env.example .env

# API
python scripts/run_api.py

# Worker (separate terminal)
python scripts/run_worker.py

# Post-process (separate terminal)
python scripts/run_postprocess.py
```

## Create a channel and start recording

```bash
curl -s -X POST http://localhost:8080/channels \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "srt-test",
    "source": {
      "protocol": "file",
      "uri": "/path/to/sample.ts"
    },
    "pipeline": {
      "profile": "ts_passthrough",
      "segment_duration_sec": 60
    },
    "output": {
      "container": "mpegts"
    }
  }'

# Start / stop
curl -X POST http://localhost:8080/channels/{id}/start
curl -X POST http://localhost:8080/channels/{id}/stop

# Browse assets
curl http://localhost:8080/assets
```

## Project layout

```
ingest_farm/
  pipeline/
    builder.py              # Composes stages from channel profile
    stages/
      source/               # SRT, UDP, file
      processing/           # Passthrough (Phase 1)
      output/               # TS capture
    encoders/
      registry.py           # Swappable encoder plugins
  worker/                   # GStreamer recorder
  orchestrator/             # Redis job scheduler
  postprocess/              # Asset catalog + proxy (stub)
  api/                      # FastAPI control plane
```

## Roadmap

- [ ] `transcode_remux` profile — demux, encoder registry, remux (MKV/MXF)
- [ ] External SDK adapters — MainConcept, Insync
- [ ] HLS proxy + thumbnail generation in post-process
- [ ] RTMP, HLS pull source stages
