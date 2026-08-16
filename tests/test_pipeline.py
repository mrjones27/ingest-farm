from ingest_farm.pipeline.builder import PipelineBuilder
from ingest_farm.pipeline.encoders.registry import default_registry
from ingest_farm.schemas import (
    ChannelConfig,
    OutputConfig,
    PipelineConfig,
    PipelineProfile,
    SourceConfig,
    SourceProtocol,
)


def test_default_profile_is_ts_passthrough() -> None:
    config = ChannelConfig(
        name="test",
        source=SourceConfig(protocol=SourceProtocol.SRT, uri="srt://0.0.0.0:9000"),
    )
    assert config.pipeline.profile == PipelineProfile.TS_PASSTHROUGH
    assert config.output.container == "mpegts"


def test_channel_config_from_dict() -> None:
    config = ChannelConfig.model_validate(
        {
            "name": "udp-1",
            "source": {"protocol": "udp", "uri": "udp://0.0.0.0:5000"},
            "pipeline": {"profile": "ts_passthrough", "segment_duration_sec": 60},
            "output": {"container": "mpegts"},
        }
    )
    assert config.source.protocol == SourceProtocol.UDP
    assert config.pipeline.segment_duration_sec == 60


def test_encoder_registry_includes_gstreamer_and_stubs() -> None:
    registry = default_registry()
    assert "gstreamer:x264enc" in registry.list_encoders()
    assert "gstreamer:avenc_aac" in registry.list_encoders()
    assert "mainconcept:h264" in registry.list_encoders()
    assert "insync" in registry.list_framerate_converters()


def test_external_encoder_fails_fast() -> None:
    registry = default_registry()
    encoder = registry.get_encoder("mainconcept:h264")
    try:
        encoder.build_video_element({})
        raise AssertionError("expected NotImplementedError")
    except NotImplementedError:
        pass


def test_normalize_srt_stats_flattens_caller() -> None:
    from ingest_farm.common.gst_stats import normalize_srt_stats

    raw = {
        "bytes-received-total": 1000,
        "callers": [
            {
                "rtt-ms": 12.5,
                "bandwidth-mbps": 40.0,
                "receive-rate-mbps": 8.2,
                "packets-received-lost": 3,
                "negotiated-latency-ms": 120,
            }
        ],
    }
    out = normalize_srt_stats(raw)
    assert out["available"] is True
    assert out["rtt-ms"] == 12.5
    assert out["receive-rate-mbps"] == 8.2
    assert out["caller_count"] == 1


def test_normalize_srt_stats_empty() -> None:
    from ingest_farm.common.gst_stats import normalize_srt_stats

    assert normalize_srt_stats({}) == {}


def test_udp_rtp_transport_config_accepted() -> None:
    config = ChannelConfig.model_validate(
        {
            "name": "rtp-1",
            "source": {
                "protocol": "udp",
                "uri": "udp://0.0.0.0:5004",
                "config": {"transport": "rtp-h264"},
            },
        }
    )
    assert config.source.config["transport"] == "rtp-h264"
