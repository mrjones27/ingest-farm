from pathlib import Path

from sqlalchemy import BigInteger

from ingest_farm.models import Recording
from ingest_farm.postprocess.main import concat_mpegts_segments
from ingest_farm.schemas import PipelineConfig, SourceConfig, SourceProtocol


def test_srt_child_inits_gst_before_pydantic_imports() -> None:
    """Importing pydantic before Gst.init() stops srtsrc from accepting callers.

    The listener still binds and encoders appear to connect, but srt_accept never
    completes, so every caller times out and reconnects forever.
    """
    source = Path("ingest_farm/worker/srt_session_proc.py").read_text(encoding="utf-8")
    body = source[source.index("def main("):]
    assert body.index("Gst.init(None)") < body.index("from ingest_farm.config import get_settings")


def test_recording_byte_size_is_bigint() -> None:
    col = Recording.__table__.c.byte_size
    assert isinstance(col.type, BigInteger)


def test_concat_mpegts_segments(tmp_path: Path) -> None:
    a = tmp_path / "segment_00000.ts"
    b = tmp_path / "segment_00001.ts"
    a.write_bytes(b"AAAA")
    b.write_bytes(b"BBBB")
    master = concat_mpegts_segments([a, b], tmp_path / "master.ts")
    assert master.read_bytes() == b"AAAABBBB"


def test_concat_single_segment_returns_original(tmp_path: Path) -> None:
    a = tmp_path / "segment_00000.ts"
    a.write_bytes(b"ONLY")
    master = concat_mpegts_segments([a], tmp_path / "master.ts")
    assert master == a


def test_source_uri_rejects_injection() -> None:
    try:
        SourceConfig(
            protocol=SourceProtocol.SRT,
            uri='srt://0.0.0.0:9000" ! fakesink',
        )
        raise AssertionError("expected validation error")
    except Exception as exc:
        assert "!" in str(exc) or '"' in str(exc) or "uri" in str(exc).lower()


def test_write_thumbs_atomic(tmp_path: Path) -> None:
    from ingest_farm.pipeline.stages.output.preview import write_thumbs

    dest = tmp_path / "thumb.jpg"
    write_thumbs(dest, b"\xff\xd8" + b"\x00" * 120 + b"\xff\xd9")
    assert dest.is_file()
    assert dest.stat().st_size >= 100
    assert (tmp_path / "thumb.ok.jpg").is_file()


def test_pipeline_rejects_transcode_remux() -> None:
    try:
        PipelineConfig(profile="transcode_remux")
        raise AssertionError("expected validation error")
    except Exception:
        pass


def test_gst_env_defaults_and_latency() -> None:
    from ingest_farm.config import Settings

    s = Settings(_env_file=None)
    assert s.gst_srt_latency_ms == 500
    # False makes srtsrc reject every incoming caller.
    assert s.gst_srt_authentication is True
    assert s.leaky("passthrough") == 0
    assert s.leaky("preview") == 1
    assert s.queue_time_ns("passthrough") == 30_000_000_000
    assert s.gst_passthrough_queue_bytes == 67_108_864
    assert s.gst_preview_queue_buffers == 8
    assert s.gst_udp_buffer_size == 8_388_608
    assert s.gst_proxy_x264_tune == ""
    assert s.gst_proxy_key_int_max == 200
    assert s.ensure_srt_latency("srt://0.0.0.0:5001") == "srt://0.0.0.0:5001?latency=500"
    assert s.ensure_srt_latency("srt://0.0.0.0:5001?mode=listener") == (
        "srt://0.0.0.0:5001?mode=listener&latency=500"
    )
    assert "latency=120" in s.ensure_srt_latency("srt://0.0.0.0:5001?latency=120")


def test_list_segments_ignores_master_ts(tmp_path: Path) -> None:
    from ingest_farm.common.storage import LocalStorage

    (tmp_path / "segment_00000.ts").write_bytes(b"A")
    (tmp_path / "segment_00001.ts").write_bytes(b"B")
    (tmp_path / "master.ts").write_bytes(b"NOPE")
    segs = LocalStorage(tmp_path).list_segments(tmp_path)
    assert [p.name for p in segs] == ["segment_00000.ts", "segment_00001.ts"]


def test_gst_leaky_modes() -> None:
    from ingest_farm.config import Settings

    assert Settings(gst_passthrough_leaky="none").leaky("passthrough") == 0
    assert Settings(gst_passthrough_leaky="downstream").leaky("passthrough") == 2
    assert Settings(gst_preview_leaky="upstream").leaky("preview") == 1
    assert Settings(etr290_queue_leaky="upstream").leaky("etr290") == 1


def test_hls_playlist_vod_finalize() -> None:
    from ingest_farm.postprocess.proxy import finalize_vod_playlist_text, rewrite_proxy_playlist

    text = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4.0,\nseg0.ts\n#EXTINF:3.2,\nseg1.ts\n"
    finalized = finalize_vod_playlist_text(text)
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in finalized
    assert finalized.strip().endswith("#EXT-X-ENDLIST")
    assert finalized.count("#EXT-X-ENDLIST") == 1

    rewritten = rewrite_proxy_playlist(text, "asset-1")
    assert "/api/assets/asset-1/proxy/seg0.ts" in rewritten
    assert "#EXT-X-ENDLIST" in rewritten


def test_playlist_duration_ms(tmp_path: Path) -> None:
    from ingest_farm.postprocess.proxy import playlist_duration_ms

    playlist = tmp_path / "playlist.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXTINF:4.0,\na.ts\n#EXTINF:3.25,\nb.ts\n#EXT-X-ENDLIST\n",
        encoding="utf-8",
    )
    assert playlist_duration_ms(playlist) == 7250


def test_resolve_asset_duration_prefers_media_not_wall() -> None:
    from ingest_farm.postprocess.main import resolve_asset_duration_ms, sane_media_ms

    assert sane_media_ms(2**64) is None
    assert sane_media_ms(-1) is None
    ms, src = resolve_asset_duration_ms(playlist_ms=127_000, discover_ms=126_000, wall_ms=206_000)
    assert (ms, src) == (127_000, "hls_playlist")
    ms, src = resolve_asset_duration_ms(playlist_ms=None, discover_ms=127_000, wall_ms=206_000)
    assert (ms, src) == (127_000, "discoverer")
    ms, src = resolve_asset_duration_ms(playlist_ms=None, discover_ms=None, wall_ms=206_000)
    assert (ms, src) == (206_000, "wall_clock")
    ms, src = resolve_asset_duration_ms(playlist_ms=None, discover_ms=10_000_000, wall_ms=206_000)
    assert (ms, src) == (206_000, "wall_clock")
