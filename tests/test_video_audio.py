import subprocess

import pytest
from video_fakes import FakeRunner, write_voice

from stickman.video import ffmpeg
from stickman.video.audio import build_voice, missing_voice
from stickman.video.config import LEAD, SHOT_GAP
from stickman.video.errors import VideoError
from stickman.video.text import spoken
from stickman.video.workdir import WorkDir

SEGMENTS = (("s01", ("你好，世界。",)), ("s02", ("想起ta。", "再见。")))


def _work(tmp_path):
    work = WorkDir(tmp_path)
    for sid, lines in SEGMENTS:
        write_voice(work, sid, spoken("".join(lines)))
    return work


def test_build_voice_places_shots_and_subtitles_on_one_timeline(tmp_path):
    work = _work(tmp_path)
    runner = FakeRunner(durations={str(work.trimmed("s01")): 1.5, str(work.voice_wav): 6.0})

    timeline = build_voice(work, SEGMENTS, runner)

    assert timeline.shot_starts == (LEAD, pytest.approx(LEAD + 1.5 + SHOT_GAP))
    assert timeline.total == 6.0
    assert [e[2] for e in timeline.events] == ["你好", "世界", "想起ta", "再见"]
    assert timeline.events[2][0] >= timeline.shot_starts[1]
    assert work.voice_wav.exists() and work.trimmed("s02").exists()


def test_build_voice_trims_each_segment_and_concats_with_silences(tmp_path):
    work = _work(tmp_path)
    runner = FakeRunner()

    build_voice(work, SEGMENTS, runner)

    trims = [c for c in runner.ffmpeg_calls() if "atrim" in " ".join(c) and c[-1].endswith(".wav") and "voice_trim" in c[-1]]
    assert len(trims) == 2
    final = runner.ffmpeg_calls()[-1]
    graph = final[final.index("-filter_complex") + 1]
    assert final[-1] == str(work.voice_wav)
    assert "concat=n=5:v=0:a=1" in graph  # 开头静音 + 2 段 + 中间静音 + 结尾静音


def test_stale_voice_names_the_shot(tmp_path):
    work = _work(tmp_path)
    write_voice(work, "s02", "旧的旁白")

    with pytest.raises(VideoError) as exc:
        build_voice(work, SEGMENTS, FakeRunner())

    assert "s02" in str(exc.value)


def test_missing_voice_lists_shots_without_audio_or_metadata(tmp_path):
    work = _work(tmp_path)
    work.voice_meta("s02").unlink()

    assert missing_voice(work, ("s01", "s02", "s03")) == ("s02", "s03")
    with pytest.raises(VideoError):
        build_voice(work, SEGMENTS, FakeRunner())


def test_broken_metadata_json_is_reported(tmp_path):
    work = _work(tmp_path)
    work.voice_meta("s01").write_text("{oops", encoding="utf-8")

    with pytest.raises(VideoError) as exc:
        build_voice(work, SEGMENTS, FakeRunner())

    assert "s01" in str(exc.value)


def test_run_command_returns_stdout_and_truncates_errors(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[0] == "bad":
            return subprocess.CompletedProcess(cmd, 1, "", "x" * 1000 + "END")
        return subprocess.CompletedProcess(cmd, 0, "1.5\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert ffmpeg.run_command(["ffprobe", "a"]) == "1.5\n"
    with pytest.raises(VideoError) as exc:
        ffmpeg.run_command(["bad"])
    assert str(exc.value).endswith("END") and len(str(exc.value)) < 400


def test_run_command_reports_missing_binary(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(VideoError) as exc:
        ffmpeg.run_command(["ffmpeg"])
    assert "ffmpeg" in str(exc.value)


def test_probe_helpers_parse_numbers_and_reject_garbage(tmp_path):
    runner = FakeRunner(durations={"a.mp3": 2.5}, frames={"c.mp4": 140})

    assert ffmpeg.probe_duration("a.mp3", runner) == 2.5
    assert ffmpeg.count_frames("c.mp4", runner) == 140
    with pytest.raises(VideoError):
        ffmpeg.probe_duration("x", lambda cmd: "N/A\n")
    with pytest.raises(VideoError):
        ffmpeg.count_frames("x", lambda cmd: "\n")


def test_filter_escape_and_concat_line_quote_special_characters():
    assert ffmpeg.filter_escape("/a b/it's:x,[y];z.ass") == "/a b/it\\\\\\'s\\\\:x\\,\\[y\\]\\;z.ass"
    assert ffmpeg.concat_line("/tmp/it's.mp4") == "file '/tmp/it'\\''s.mp4'\n"
