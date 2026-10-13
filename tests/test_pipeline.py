import json
import threading
import time
from dataclasses import replace

import pytest

from bili_collect.bili import AccessDeniedError, BiliError
from bili_collect.models import VideoEntry
from bili_collect import pipeline
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


def read_info(directory):
    return json.loads((directory / "transcript.info.json").read_text(encoding="utf-8"))


def test_whisper_is_main_transcript_and_official_subtitles_are_saved_alongside(make_ctx, tmp_path):
    client = FakeClient(subtitles=("ai-zh", (Segment(0.0, 1.0, "官方字幕"),)))

    result = process_video(make_ctx(client), "BV1", transcribe=True)

    vdir = tmp_path / MID / "BV1"
    assert "你好" in (vdir / "transcript.txt").read_text(encoding="utf-8")  # Whisper 的结果
    assert read_info(vdir)["source"] == "whisper"
    assert "官方字幕" in (vdir / "subtitles.ai-zh.srt").read_text(encoding="utf-8")
    assert result.transcript_source == "whisper"
    assert result.subtitles == vdir / "subtitles.ai-zh.srt"


def test_no_subtitle_file_when_video_has_none(make_ctx, tmp_path):
    client = FakeClient(subtitles=None)

    result = process_video(make_ctx(client), "BV1", transcribe=True)

    assert ("subtitles", "BV1") in client.calls
    assert not list((tmp_path / MID / "BV1").glob("subtitles.*"))
    assert result.subtitles is None
    assert result.transcript_source == "whisper"


def test_subtitle_request_failure_does_not_block_whisper(make_ctx, tmp_path):
    client = FakeClient(subtitle_error=BiliError("subtitle api down"))

    process_video(make_ctx(client), "BV1", transcribe=True)

    assert read_info(tmp_path / MID / "BV1")["source"] == "whisper"


def test_subtitle_risk_control_is_not_swallowed(make_ctx):
    client = FakeClient(subtitle_error=AccessDeniedError("blocked"))

    with pytest.raises(AccessDeniedError):
        process_video(make_ctx(client), "BV1", transcribe=True)


def test_subtitles_can_be_disabled(make_ctx, tmp_path):
    client = FakeClient(subtitles=("ai-zh", (Segment(0.0, 1.0, "官方"),)))
    ctx = replace(make_ctx(client), use_subtitles=False, transcriber_name="whisper:m")

    process_video(ctx, "BV1", transcribe=True)

    assert ("subtitles", "BV1") not in client.calls
    assert read_info(tmp_path / MID / "BV1")["source"] == "whisper:m"
    assert not list((tmp_path / MID / "BV1").glob("subtitles.*"))


def test_process_video_skips_existing_transcript(make_ctx, tmp_path):
    vdir = tmp_path / MID / "BV1"
    vdir.mkdir(parents=True)
    (vdir / "transcript.json").write_text("[]", encoding="utf-8")
    client = FakeClient()

    def boom(path):
        raise AssertionError("should not transcribe again")

    result = process_video(replace(make_ctx(client), transcribe_audio=boom), "BV1", transcribe=True)

    assert ("audio", "BV1") not in client.calls
    assert ("subtitles", "BV1") not in client.calls
    assert result.transcript_reused is True


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


class ConcurrencyProbe:
    """记录同一时刻有多少个调用在执行。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.threads = set()

    def __enter__(self):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.threads.add(threading.get_ident())

    def __exit__(self, *exc):
        with self.lock:
            self.active -= 1


class SlowClient(FakeClient):
    def __init__(self, *args, barrier=None, probe=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.barrier = barrier
        self.probe = probe or ConcurrencyProbe()

    def fetch_meta(self, bvid):
        with self.probe:
            if self.barrier:
                self.barrier.wait(timeout=5)  # 只有两个 worker 同时在跑才能通过
            time.sleep(0.02)
            return super().fetch_meta(bvid)


def entries(n):
    return [VideoEntry(f"BV{i}", str(i)) for i in range(1, n + 1)]


def test_collect_runs_network_stage_concurrently_up_to_jobs(make_ctx):
    client = SlowClient(listing=entries(6))

    report = collect_user(make_ctx(client), MID, jobs=2)

    assert sorted(report.done) == [f"BV{i}" for i in range(1, 7)]
    assert client.probe.peak <= 2  # 下限由 Barrier 测试保证


def test_collect_two_workers_really_overlap(make_ctx):
    client = SlowClient(listing=entries(2), barrier=threading.Barrier(2))

    report = collect_user(make_ctx(client), MID, jobs=2)

    assert sorted(report.done) == ["BV1", "BV2"]


def test_collect_transcribes_serially_on_calling_thread(make_ctx):
    probe = ConcurrencyProbe()

    def transcribe(path):
        with probe:
            time.sleep(0.02)
            return (Segment(0.0, 1.0, "你好"),)

    client = SlowClient(listing=entries(5))
    ctx = replace(make_ctx(client), transcribe_audio=transcribe)

    report = collect_user(ctx, MID, transcribe=True, jobs=3)

    assert len(report.done) == 5
    assert probe.peak == 1
    assert probe.threads == {threading.get_ident()}


def test_collect_report_follows_listing_order(make_ctx):
    report = collect_user(make_ctx(SlowClient(listing=entries(5))), MID, jobs=3)

    assert report.done == tuple(f"BV{i}" for i in range(1, 6))


def test_collect_parallel_records_failures_and_continues(make_ctx):
    client = SlowClient(listing=entries(4), failing={"BV2"})

    report = collect_user(make_ctx(client), MID, jobs=2)

    assert report.done == ("BV1", "BV3", "BV4")
    assert [f.bvid for f in report.failed] == ["BV2"]


def test_collect_parallel_transcription_failure_is_recorded(make_ctx):
    def transcribe(path):
        if "BV2" in str(path):
            raise RuntimeError("gpu out of memory")
        return (Segment(0.0, 1.0, "你好"),)

    ctx = replace(make_ctx(SlowClient(listing=entries(3))), transcribe_audio=transcribe)

    report = collect_user(ctx, MID, transcribe=True, jobs=2)

    assert report.done == ("BV1", "BV3")
    assert report.failed == (Failure("BV2", "RuntimeError: gpu out of memory"),)


class ScriptedClient(FakeClient):
    """BV1 立即就绪（主线程马上进入转写）；BV2 在 0.2 秒后触发风控；其余每个耗时 0.3 秒。"""

    def fetch_meta(self, bvid):
        if bvid == "BV2":
            self.calls.append(("meta", bvid))
            time.sleep(0.2)
            raise AccessDeniedError("blocked")
        if bvid != "BV1":
            time.sleep(0.3)
        return super().fetch_meta(bvid)


def test_risk_control_stops_workers_even_while_whisper_is_busy(make_ctx):
    client = ScriptedClient(listing=entries(10))

    def slow_whisper(path):
        time.sleep(0.6)  # 主线程忙于转写时，worker 必须自己停下
        return (Segment(0.0, 1.0, "你好"),)

    ctx = replace(make_ctx(client), transcribe_audio=slow_whisper)

    with pytest.raises(AccessDeniedError):
        collect_user(ctx, MID, transcribe=True, jobs=2)

    started = sorted(c[1] for c in client.calls if c[0] == "meta")
    # BV3 在风控前已开始；风控发生时主线程还在转写 BV1，此后不应再有新请求
    assert started == ["BV1", "BV2", "BV3"]


def test_network_stage_overlaps_transcription(make_ctx):
    later_started = threading.Event()
    overlapped = []

    class Client(FakeClient):
        def fetch_meta(self, bvid):
            if bvid == "BV2":
                later_started.set()
            return super().fetch_meta(bvid)

    def whisper(path):
        if "BV1" in str(path):
            overlapped.append(later_started.wait(timeout=2))  # 串行实现会在这里超时
        return (Segment(0.0, 1.0, "你好"),)

    ctx = replace(make_ctx(Client(listing=entries(2))), transcribe_audio=whisper)

    collect_user(ctx, MID, transcribe=True, jobs=1)

    assert overlapped == [True]


def test_prefetch_is_bounded_while_whisper_is_slow(make_ctx):
    client = FakeClient(listing=entries(12))
    seen_while_busy = []

    def whisper(path):
        time.sleep(0.2)
        seen_while_busy.append(sum(1 for c in client.calls if c[0] == "meta"))
        return (Segment(0.0, 1.0, "你好"),)

    collect_user(replace(make_ctx(client), transcribe_audio=whisper), MID, transcribe=True, jobs=2)

    # 正在转写的 1 个 + 最多 jobs+1 个已准备好等待转写的
    assert seen_while_busy[0] <= 1 + (2 + 1)


def test_collect_dedupes_listing(make_ctx):
    client = FakeClient(listing=[VideoEntry("BV1", "一"), VideoEntry("BV1", "一"), VideoEntry("BV2", "二")])

    report = collect_user(make_ctx(client), MID, jobs=2)

    assert report.done == ("BV1", "BV2")
    assert [c for c in client.calls if c == ("meta", "BV1")] == [("meta", "BV1")]


def test_audio_and_subtitles_both_honoured_and_resumable(make_ctx, tmp_path):
    def client():
        return FakeClient(listing=[VideoEntry("BV1", "一")], subtitles=("ai-zh", (Segment(0.0, 1.0, "官方"),)))

    report = collect_user(make_ctx(client()), MID, audio=True, transcribe=True)

    vdir = tmp_path / MID / "BV1"
    assert report.done == ("BV1",)
    assert (vdir / "audio.wav").exists()
    assert read_info(vdir)["source"] == "whisper"
    assert (vdir / "subtitles.ai-zh.srt").exists()
    assert collect_user(make_ctx(client()), MID, audio=True, transcribe=True).skipped == ("BV1",)


def test_missing_subtitles_do_not_make_video_incomplete(make_ctx):
    def client():
        return FakeClient(listing=[VideoEntry("BV1", "一")], subtitles=None)

    collect_user(make_ctx(client()), MID, transcribe=True)

    assert collect_user(make_ctx(client()), MID, transcribe=True).skipped == ("BV1",)


def test_whisper_reuses_audio_extracted_for_audio_flag(make_ctx, tmp_path):
    client = FakeClient()

    process_video(make_ctx(client), "BV1", audio=True, transcribe=True)

    assert [c for c in client.calls if c[0] == "audio"] == [("audio", "BV1")]
    assert read_info(tmp_path / MID / "BV1")["source"] == "whisper"


def test_existing_info_file_with_unexpected_shape_is_tolerated(make_ctx, tmp_path):
    vdir = tmp_path / MID / "BV1"
    vdir.mkdir(parents=True)
    (vdir / "transcript.json").write_text("[]", encoding="utf-8")
    (vdir / "transcript.info.json").write_text("[1, 2]", encoding="utf-8")

    result = process_video(make_ctx(FakeClient()), "BV1", transcribe=True)

    assert result.transcript_source is None


def test_collect_parallel_keeps_index_consistent(make_ctx, tmp_path, monkeypatch):
    real_read = pipeline.read_index

    def slow_read(path):
        entries_read = real_read(path)
        time.sleep(0.02)  # 放大读-改-写窗口：没有锁时并发更新会互相覆盖
        return entries_read

    monkeypatch.setattr(pipeline, "read_index", slow_read)

    collect_user(make_ctx(SlowClient(listing=entries(8))), MID, jobs=4)

    assert sorted(e.bvid for e in read_index(tmp_path / MID / "videos.jsonl")) == sorted(f"BV{i}" for i in range(1, 9))
    assert all(e.title == "标题" for e in read_index(tmp_path / MID / "videos.jsonl"))


def test_collect_rejects_invalid_jobs(make_ctx):
    with pytest.raises(ValueError):
        collect_user(make_ctx(FakeClient()), MID, jobs=0)
