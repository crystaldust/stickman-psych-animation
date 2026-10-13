import subprocess
from pathlib import Path

import pytest

from bili_collect.media import MediaError, extract_audio


def writing_runner(calls):
    def runner(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"wav")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return runner


def test_extract_audio_writes_part_file_then_renames(tmp_path):
    src = tmp_path / "source.m4a"
    src.write_bytes(b"x")
    dst = tmp_path / "audio.wav"
    calls = []

    result = extract_audio(src, dst, runner=writing_runner(calls))

    assert result == dst
    assert dst.read_bytes() == b"wav"
    cmd = calls[0]
    assert cmd[0] == "ffmpeg"
    assert cmd[cmd.index("-i") + 1] == str(src.absolute())
    assert cmd[cmd.index("-ar") + 1] == "16000"
    assert cmd[cmd.index("-ac") + 1] == "1"
    assert cmd[cmd.index("-f") + 1] == "wav"
    assert cmd[-1] == str(dst.absolute()) + ".part"
    assert not Path(cmd[-1]).exists()


def test_extract_audio_removes_stale_part_file(tmp_path):
    src = tmp_path / "s.m4a"
    src.write_bytes(b"x")
    stale = tmp_path / "audio.wav.part"
    stale.write_bytes(b"half")

    extract_audio(src, tmp_path / "audio.wav", runner=writing_runner([]))

    assert (tmp_path / "audio.wav").read_bytes() == b"wav"
    assert not stale.exists()


def test_extract_audio_skips_when_output_exists(tmp_path):
    src = tmp_path / "s.m4a"
    src.write_bytes(b"x")
    dst = tmp_path / "audio.wav"
    dst.write_bytes(b"done")

    def runner(cmd, **kwargs):
        raise AssertionError("should not run")

    assert extract_audio(src, dst, runner=runner) == dst


def test_extract_audio_interrupted_leaves_no_output(tmp_path):
    src = tmp_path / "s.m4a"
    src.write_bytes(b"x")
    dst = tmp_path / "audio.wav"

    def runner(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(b"half")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        extract_audio(src, dst, runner=runner)

    assert not dst.exists()
    assert not (tmp_path / "audio.wav.part").exists()


def test_extract_audio_reports_ffmpeg_failure(tmp_path):
    src = tmp_path / "s.m4a"
    src.write_bytes(b"x")

    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "Invalid data found")

    with pytest.raises(MediaError, match="Invalid data found"):
        extract_audio(src, tmp_path / "audio.wav", runner=runner)
    assert not (tmp_path / "audio.wav").exists()


def test_extract_audio_missing_ffmpeg(tmp_path):
    src = tmp_path / "s.m4a"
    src.write_bytes(b"x")

    def runner(cmd, **kwargs):
        raise FileNotFoundError("ffmpeg")

    with pytest.raises(MediaError, match="brew install ffmpeg"):
        extract_audio(src, tmp_path / "audio.wav", runner=runner)


def test_extract_audio_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract_audio(tmp_path / "nope.m4a", tmp_path / "audio.wav")
