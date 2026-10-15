import json

import pytest

from stickman import cli as root_cli
from stickman.video import cli
from stickman.video.config import STYLE_PROMPT
from stickman.video.draft import parse_draft
from stickman.video.errors import VideoError
from stickman.video.workdir import safe_id, safe_slug

DRAFT = """---
title: 测试
status: draft
---

有一个人，你们从来没有真正在一起过。
> 画面：深夜，火柴人坐在窗边

可这么多年过去，你还是会想起ta。
想起ta那天穿的外套，

> 画面：两个火柴人并肩走在路灯下
奇怪的是，真正谈过的那几段，
你反而记不太清了。
> 画面：几张合照慢慢褪色
"""


def test_parse_draft_strips_front_matter_blank_and_cue_lines():
    draft = parse_draft(DRAFT)

    assert draft.lines == (
        "有一个人，你们从来没有真正在一起过。",
        "可这么多年过去，你还是会想起ta。",
        "想起ta那天穿的外套，",
        "奇怪的是，真正谈过的那几段，",
        "你反而记不太清了。",
    )


def test_each_cue_closes_a_block_starting_at_its_first_line():
    draft = parse_draft(DRAFT)

    assert [(b.line, b.cue) for b in draft.blocks] == [
        (1, "深夜，火柴人坐在窗边"),
        (2, "两个火柴人并肩走在路灯下"),
        (4, "几张合照慢慢褪色"),
    ]
    assert draft.warnings == ()


def test_narration_after_last_cue_becomes_a_final_shot_with_a_warning():
    draft = parse_draft("第一句。\n> 画面：A\n第二句。\n")

    assert [(b.line, b.cue) for b in draft.blocks] == [(1, "A"), (2, "")]
    assert len(draft.warnings) == 1


def test_draft_without_front_matter_is_fine():
    assert parse_draft("一句话。\n> B\n").blocks[0].cue == "B"


@pytest.mark.parametrize("text", ["", "---\na: 1\n---\n", "> 画面：先有画面\n一句话。\n", "一句。\n> A\n> B\n"])
def test_cue_without_narration_or_empty_draft_is_an_error(text):
    with pytest.raises(VideoError):
        parse_draft(text)


def test_unterminated_front_matter_is_an_error():
    with pytest.raises(VideoError):
        parse_draft("---\ntitle: x\n一句话。\n")


@pytest.mark.parametrize("value", ["../x", "A B", "", "-x", "a/b", "x" * 65, "中文"])
def test_unsafe_slugs_and_ids_are_rejected(value):
    with pytest.raises(VideoError):
        safe_slug(value)
    with pytest.raises(VideoError):
        safe_id(value)


def test_safe_slug_accepts_draft_stems():
    assert safe_slug("20261014-jie-jing") == "20261014-jie-jing"
    assert safe_id("s01") == "s01"


def _write_draft(tmp_path, text=DRAFT, name="20261014-test.md"):
    path = tmp_path / "drafts" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_init_writes_narration_and_shot_skeleton(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    draft = _write_draft(tmp_path)

    code = cli.main(["init", str(draft)])

    work = tmp_path / "data" / "videos" / "20261014-test"
    assert code == 0
    assert (work / "narration.txt").read_text(encoding="utf-8").splitlines()[0] == "有一个人，你们从来没有真正在一起过。"
    spec = json.loads((work / "shots.json").read_text(encoding="utf-8"))
    assert spec["style"] == STYLE_PROMPT
    assert spec["shots"][1] == {"id": "s02", "line": 2, "cue": "两个火柴人并肩走在路灯下", "bg": "", "scene": ""}
    assert "3" in capsys.readouterr().out


def test_init_refuses_to_overwrite_without_force(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    draft = _write_draft(tmp_path)
    assert cli.main(["init", str(draft)]) == 0

    assert cli.main(["init", str(draft)]) == 1
    assert "--force" in capsys.readouterr().err
    assert cli.main(["init", str(draft), "--force"]) == 0


def test_init_rejects_unsafe_slug_and_paths_outside_project(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    draft = _write_draft(tmp_path)

    assert cli.main(["init", str(draft), "--slug", "../evil"]) == 1
    assert cli.main(["init", str(draft), "--videos-dir", "../outside"]) == 1
    assert not (tmp_path.parent / "outside").exists()


def test_init_reports_missing_draft(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert cli.main(["init", "drafts/nope.md"]) == 1
    assert "错误" in capsys.readouterr().err


def test_root_cli_lists_and_forwards_video(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "main", lambda argv, prog="stickman video": calls.append(argv) or 0)

    assert root_cli.main(["video", "check", "x"]) == 0
    assert calls == [["check", "x"]]
    assert "video" in root_cli.COMMANDS
