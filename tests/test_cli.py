from pathlib import Path

import pytest

from stickman.collect.bilibili import cli
from stickman.collect.bilibili.bili import AccessDeniedError
from stickman.collect.bilibili.models import VideoEntry
from stickman.collect.bilibili.pipeline import CollectReport, Failure, VideoResult


@pytest.fixture
def recorder(monkeypatch):
    calls = []

    def fake_list(ctx, mid, limit=None):
        calls.append(("list", mid, limit))
        return [VideoEntry("BV1", "一", duration=61.0, upload_date="20251021")]

    def fake_process(ctx, bvid, *, video=False, audio=False, transcribe=False):
        calls.append(("fetch", bvid, video, audio, transcribe))
        return VideoResult(bvid=bvid, mid="42", directory=Path("/tmp/x"), transcript=None)

    def fake_collect(ctx, mid, *, limit=None, video=False, audio=False, transcribe=False, jobs=1):
        calls.append(("collect", mid, limit, video, audio, transcribe, jobs))
        return CollectReport(done=("BV1",), skipped=(), failed=(Failure("BV2", "boom"),))

    monkeypatch.setattr(cli, "list_user", fake_list)
    monkeypatch.setattr(cli, "process_video", fake_process)
    monkeypatch.setattr(cli, "collect_user", fake_collect)
    monkeypatch.setattr(cli, "build_context", lambda args: object())
    return calls


def test_list_command(recorder, capsys):
    code = cli.main(["list", "https://space.bilibili.com/42", "--limit", "3"])

    assert code == 0
    assert recorder == [("list", "42", 3)]
    assert "BV1" in capsys.readouterr().out


def test_fetch_command_flags(recorder):
    code = cli.main(["fetch", "BV1CSWXzAEys", "--video", "--transcribe"])

    assert code == 0
    assert recorder == [("fetch", "BV1CSWXzAEys", True, False, True)]


def test_collect_reports_failures_with_nonzero_exit(recorder, capsys):
    code = cli.main(["collect", "42", "--transcribe"])

    assert code == 1
    assert recorder == [("collect", "42", None, False, False, True, 1)]
    assert "BV2" in capsys.readouterr().err


def test_collect_accepts_all_three_output_flags(recorder):
    cli.main(["collect", "42", "--audio", "--video", "--transcribe"])

    assert recorder == [("collect", "42", None, True, True, True, 1)]


def test_collect_jobs_option(recorder):
    cli.main(["collect", "42", "--jobs", "3"])

    assert recorder[0][-1] == 3


@pytest.mark.parametrize("jobs", ["0", "5", "-1"])
def test_collect_jobs_is_bounded(recorder, jobs):
    with pytest.raises(SystemExit):
        cli.main(["collect", "42", "--jobs", jobs])


def test_wrong_target_kind_is_rejected(recorder, capsys):
    assert cli.main(["list", "BV1CSWXzAEys"]) == 2
    assert cli.main(["fetch", "42"]) == 2
    assert recorder == []
    assert "up" in capsys.readouterr().err


def test_invalid_target(recorder, capsys):
    assert cli.main(["fetch", "not-a-target"]) == 2


def test_access_denied_prints_cookie_hint(monkeypatch, capsys):
    def denied(ctx, mid, limit=None):
        raise AccessDeniedError("需要 cookies")

    monkeypatch.setattr(cli, "list_user", denied)
    monkeypatch.setattr(cli, "build_context", lambda args: object())

    assert cli.main(["list", "42"]) == 1
    assert "--cookies-from-browser" in capsys.readouterr().err


def test_limit_must_be_positive(recorder):
    with pytest.raises(SystemExit):
        cli.main(["list", "42", "--limit", "0"])


def test_noisy_third_party_loggers_are_quieted(recorder):
    import logging

    cli.main(["list", "42"])

    for name in cli.NOISY_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


def test_cookie_options_are_mutually_exclusive(recorder):
    with pytest.raises(SystemExit):
        cli.main(["--cookies-from-browser", "chrome", "--cookies", "c.txt", "list", "42"])


def test_sleep_must_not_be_negative(recorder):
    with pytest.raises(SystemExit):
        cli.main(["--sleep", "-1", "list", "42"])


def test_short_link_failure_is_reported_without_traceback(monkeypatch, recorder, capsys):
    def offline(raw):
        raise cli.TargetError("短链解析失败：offline")

    monkeypatch.setattr(cli, "parse_target", offline)

    assert cli.main(["fetch", "https://b23.tv/x"]) == 2
    assert "offline" in capsys.readouterr().err


def test_ctrl_c_exits_130(monkeypatch, recorder):
    def interrupted(ctx, mid, limit=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "list_user", interrupted)

    assert cli.main(["list", "42"]) == 130


def test_missing_mlx_whisper_is_reported(monkeypatch, recorder, capsys):
    def missing(ctx, bvid, **kwargs):
        raise ModuleNotFoundError("No module named 'mlx_whisper'")

    monkeypatch.setattr(cli, "process_video", missing)

    assert cli.main(["fetch", "BV1CSWXzAEys", "--transcribe"]) == 1
    assert "Apple Silicon" in capsys.readouterr().err


def test_build_context_wires_options(tmp_path):
    args = cli.build_parser().parse_args(
        ["--data-dir", str(tmp_path), "--cookies-from-browser", "chrome", "--sleep", "1.5", "list", "42"]
    )

    ctx = cli.build_context(args)

    assert ctx.layout.root == tmp_path
    assert ctx.client.options.cookies_from_browser == "chrome"
    assert ctx.client.options.sleep_seconds == 1.5


def test_build_context_subtitle_options(tmp_path):
    args = cli.build_parser().parse_args(["--data-dir", str(tmp_path), "--no-subtitles", "--model", "m", "list", "42"])

    ctx = cli.build_context(args)

    assert ctx.use_subtitles is False
    assert ctx.transcriber_name == "whisper:m"
    assert ctx.transcribe_audio.keywords == {"model": "m"}


def test_build_context_defaults_to_subtitles(tmp_path):
    ctx = cli.build_context(cli.build_parser().parse_args(["--data-dir", str(tmp_path), "list", "42"]))

    assert ctx.use_subtitles is True


@pytest.mark.parametrize(
    "result_kwargs, expected",
    [
        ({"transcript": Path("/x/transcript.txt"), "transcript_source": "whisper:m"}, "来源：whisper:m"),
        ({"transcript": Path("/x/transcript.txt"), "transcript_reused": True}, "已有文案，跳过（旧版本生成，来源未记录）"),
        (
            {"transcript": Path("/x/transcript.txt"), "transcript_source": "whisper:m", "subtitles": Path("/x/s.srt")},
            "官方字幕对照：/x/s.srt",
        ),
    ],
)
def test_fetch_output_describes_transcript(monkeypatch, capsys, result_kwargs, expected):
    monkeypatch.setattr(cli, "build_context", lambda args: object())
    monkeypatch.setattr(
        cli, "process_video", lambda ctx, bvid, **kw: VideoResult(bvid=bvid, mid="42", directory=Path("/x"), **result_kwargs)
    )

    cli.main(["fetch", "BV1CSWXzAEys", "--transcribe"])

    assert expected in capsys.readouterr().out
