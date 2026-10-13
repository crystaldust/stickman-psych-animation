import json
from dataclasses import replace

import pytest

from bili_collect.bili import AccessDeniedError
from bili_collect.models import VideoEntry
from bili_collect.pipeline import Context, Failure, collect_user, list_user, process_video
from bili_collect.storage import Layout, read_index
from bili_collect.transcribe import Segment

from fakes import MID, FakeClient


def fake_extract(src, dst):
    dst.write_bytes(b"wav")
    return dst


def fake_transcribe(path):
    return (Segment(0.0, 1.0, "你好"),)


@pytest.fixture
def make_ctx(tmp_path):
    def _make(client):
        return Context(
            client=client,
            layout=Layout(tmp_path),
            extract_audio=fake_extract,
            transcribe_audio=fake_transcribe,
        )

    return _make


def test_process_video_meta_only(make_ctx, tmp_path):
    client = FakeClient()

    result = process_video(make_ctx(client), "BV1")

    vdir = tmp_path / MID / "BV1"
    assert json.loads((vdir / "meta.json").read_text(encoding="utf-8"))["title"] == "标题"
    assert read_index(tmp_path / MID / "videos.jsonl")[0].bvid == "BV1"
    assert result.mid == MID
    assert [c[0] for c in client.calls] == ["meta"]


def test_process_video_full(make_ctx, tmp_path):
    client = FakeClient()

    result = process_video(make_ctx(client), "BV1", video=True, transcribe=True)

    vdir = tmp_path / MID / "BV1"
    assert (vdir / "video.mp4").exists()
    assert (vdir / "audio.wav").exists()
    assert (vdir / "transcript.srt").exists()
    assert result.transcript == vdir / "transcript.txt"


def test_process_video_audio_only_extracts_wav_without_transcript(make_ctx, tmp_path):
    result = process_video(make_ctx(FakeClient()), "BV1", audio=True)

    vdir = tmp_path / MID / "BV1"
    assert (vdir / "audio.wav").exists()
    assert not (vdir / "transcript.json").exists()
    assert result.transcript is None


def test_process_video_flags_are_keyword_only(make_ctx):
    with pytest.raises(TypeError):
        process_video(make_ctx(FakeClient()), "BV1", True)


def test_process_video_skips_existing_transcript(make_ctx, tmp_path):
    vdir = tmp_path / MID / "BV1"
    vdir.mkdir(parents=True)
    (vdir / "transcript.json").write_text("[]", encoding="utf-8")
    client = FakeClient()

    def boom(path):
        raise AssertionError("should not transcribe again")

    process_video(replace(make_ctx(client), transcribe_audio=boom), "BV1", transcribe=True)

    assert ("audio", "BV1") not in client.calls


def test_process_video_meta_without_uploader_uses_unknown(make_ctx):
    result = process_video(make_ctx(FakeClient(uploader_id=None)), "BV1")

    assert result.mid == "unknown"


def test_process_video_prefers_explicit_mid(make_ctx, tmp_path):
    result = process_video(make_ctx(FakeClient(uploader_id=None)), "BV1", mid="42")

    assert result.mid == "42"
    assert (tmp_path / "42" / "BV1" / "meta.json").exists()


def test_list_user_merges_index(make_ctx, tmp_path):
    client = FakeClient(listing=[VideoEntry("BV1", "一"), VideoEntry("BV2", "二")])

    entries = list_user(make_ctx(client), MID, limit=10)

    assert [e.bvid for e in entries] == ["BV1", "BV2"]
    assert len(read_index(tmp_path / MID / "videos.jsonl")) == 2
    assert client.calls[0] == ("list", MID, 10)
    assert ("meta", "BV1") not in client.calls


def test_list_user_enriches_untitled_entries_from_meta(make_ctx, tmp_path):
    client = FakeClient(listing=[VideoEntry("BV1", ""), VideoEntry("BV2", "")], failing={"BV2"})

    entries = list_user(make_ctx(client), MID)

    assert entries[0] == VideoEntry("BV1", "标题", duration=60.0, upload_date="20251021")
    assert entries[1] == VideoEntry("BV2", "")
    assert (tmp_path / MID / "BV1" / "meta.json").exists()
    assert read_index(tmp_path / MID / "videos.jsonl")[0].title == "标题"


def test_list_user_enrichment_stops_on_risk_control(make_ctx):
    client = FakeClient(listing=[VideoEntry("BV1", "")], failing={"BV1"}, error_cls=AccessDeniedError)

    with pytest.raises(AccessDeniedError):
        list_user(make_ctx(client), MID)


def test_list_user_reuses_titles_already_in_index(make_ctx):
    list_user(make_ctx(FakeClient(listing=[VideoEntry("BV1", "")])), MID)

    second = FakeClient(listing=[VideoEntry("BV1", "")])
    entries = list_user(make_ctx(second), MID)

    assert ("meta", "BV1") not in second.calls
    assert entries[0].title == "标题"


def test_collect_user_continues_after_failures_and_resumes(make_ctx):
    client = FakeClient(listing=[VideoEntry("BV1", "一"), VideoEntry("BV2", "二")], failing={"BV2"})

    report = collect_user(make_ctx(client), MID, transcribe=True)

    assert report.done == ("BV1",)
    assert report.failed == (Failure("BV2", "BiliError: boom BV2"),)

    client2 = FakeClient(listing=[VideoEntry("BV1", "一")])
    report2 = collect_user(make_ctx(client2), MID, transcribe=True)

    assert report2.skipped == ("BV1",)
    assert ("meta", "BV1") not in client2.calls


def test_collect_user_aborts_on_risk_control(make_ctx):
    client = FakeClient(
        listing=[VideoEntry("BV1", "一"), VideoEntry("BV2", "二")], failing={"BV1"}, error_cls=AccessDeniedError
    )

    with pytest.raises(AccessDeniedError):
        collect_user(make_ctx(client), MID)

    assert ("meta", "BV2") not in client.calls


def test_collect_user_passes_limit_and_requested_mid(make_ctx, tmp_path):
    client = FakeClient(listing=[VideoEntry("BV1", "一")], uploader_id=None)

    collect_user(make_ctx(client), "42", limit=3)

    assert client.calls[0] == ("list", "42", 3)
    assert (tmp_path / "42" / "BV1" / "meta.json").exists()


def test_collect_user_video_completion_accepts_any_container(make_ctx, tmp_path):
    vdir = tmp_path / MID / "BV1"
    vdir.mkdir(parents=True)
    (vdir / "meta.json").write_text("{}", encoding="utf-8")
    (vdir / "video.mkv").write_bytes(b"v")
    client = FakeClient(listing=[VideoEntry("BV1", "一")])

    report = collect_user(make_ctx(client), MID, video=True)

    assert report.skipped == ("BV1",)


def test_collect_user_redownloads_when_only_intermediate_video_exists(make_ctx, tmp_path):
    vdir = tmp_path / MID / "BV1"
    vdir.mkdir(parents=True)
    (vdir / "meta.json").write_text("{}", encoding="utf-8")
    (vdir / "video.f100026.mp4").write_bytes(b"v")
    client = FakeClient(listing=[VideoEntry("BV1", "一")])

    report = collect_user(make_ctx(client), MID, video=True)

    assert report.done == ("BV1",)
    assert ("video", "BV1") in client.calls


def test_collect_user_audio_flag_extracts_wav_and_counts_for_completion(make_ctx, tmp_path):
    client = FakeClient(listing=[VideoEntry("BV1", "一")])

    report = collect_user(make_ctx(client), MID, audio=True)

    assert report.done == ("BV1",)
    assert (tmp_path / MID / "BV1" / "audio.wav").exists()

    again = collect_user(make_ctx(FakeClient(listing=[VideoEntry("BV1", "一")])), MID, audio=True)
    assert again.skipped == ("BV1",)


def test_collect_user_without_audio_flag_ignores_missing_wav(make_ctx, tmp_path):
    vdir = tmp_path / MID / "BV1"
    vdir.mkdir(parents=True)
    (vdir / "meta.json").write_text("{}", encoding="utf-8")

    report = collect_user(make_ctx(FakeClient(listing=[VideoEntry("BV1", "一")])), MID)

    assert report.skipped == ("BV1",)
