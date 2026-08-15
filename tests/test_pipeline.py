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


def test_builder_supports_passthrough_sources() -> None:
    builder = PipelineBuilder()
    assert "srt" in builder.supported_protocols()
    assert "udp" in builder.supported_protocols()
    assert "file" in builder.supported_protocols()
    assert "ts_passthrough" in builder.supported_profiles()
    assert "transcode_remux" not in builder.supported_profiles()
