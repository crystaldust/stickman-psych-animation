import json

import pytest
from video_fakes import FakeRunner

from stickman.video import cli
from stickman.video.errors import VideoError
from stickman.video.publish import (
    Source,
    description_text,
    draft_authors,
    package,
    parse_publish,
)
from stickman.video.workdir import WorkDir

DRAFT = """---
title: 越怕失去一个人，越容易把ta推开
sources:
  - author: 罗伯特·默顿
    title: 自我实现的预言
    verdict: verbatim
  - author: "欧文·戈夫曼"
    title: 日常生活中的自我呈现
status: draft
---

正文第一行
author: 正文里的不算
"""

ZH = {
    "title": "越怕失去一个人，越容易把ta推开",
    "description": "ta两个小时没回消息，你已经替ta想好了分手的话。",
    "sources": [{"author": "罗伯特·默顿", "title": "社会理论与社会结构", "kind": "book", "detail": "1968",
                 "note": "《自我实现的预言》一文收在此书中", "link": ""}],
    "thumbnail": {"shot": "s01", "text": "越怕失去\n**越推开**"},
}
EN = {
    "title": "The More You Fear Losing Them",
    "description": "Two hours without a reply.",
    "sources": [{"author": "Robert K. Merton", "title": "Social Theory and Social Structure", "kind": "book",
                 "link": "https://example.com/book"}],
    "thumbnail": {"shot": "s02", "text": "**PUSHED AWAY**"},
}
SHOTS = ("s01", "s02")


def _errors(raw):
    with pytest.raises(VideoError) as info:
        parse_publish(raw, SHOTS, "publish.json")
    return str(info.value)


def test_parse_publish_reads_every_field():
    pub = parse_publish(ZH, SHOTS, "publish.json")

    assert pub.title == ZH["title"]
    assert pub.sources == (Source("罗伯特·默顿", "社会理论与社会结构", "book", "1968", "《自我实现的预言》一文收在此书中", ""),)
    assert pub.thumbnail.shot == "s01" and [line.text for line in pub.thumbnail.lines] == ["越怕失去", "越推开"]


def test_parse_publish_reports_all_problems_at_once():
    raw = {**ZH, "title": "", "sources": [{"author": "某人", "title": "某书", "kind": "blog", "link": "ftp://x"}],
           "thumbnail": {"shot": "s09", "text": "好"}}

    message = _errors(raw)

    for part in ("title", "kind", "link", "s09"):
        assert part in message


@pytest.mark.parametrize("raw", [
    [],
    {**ZH, "title": "长" * 101},
    {**ZH, "description": "  "},
    {**ZH, "sources": []},
    {**ZH, "sources": ["罗伯特·默顿"]},
    {**ZH, "sources": [{"author": "罗伯特·默顿", "kind": "book"}]},
    {**ZH, "sources": [{**ZH["sources"][0], "note": 3}]},
    {**ZH, "sources": [{**ZH["sources"][0], "isbn": "123"}]},
    {**ZH, "title": "越怕失去 <1>"},
    {**ZH, "title": None},
    {**ZH, "sources": [None, 1, "x"]},
    {**ZH, "sources": [{**ZH["sources"][0], "kind": 3}]},
    {**ZH, "sources": [{**ZH["sources"][0], "note": "a<b"}]},
    {**ZH, "sources": [{**ZH["sources"][0], "author": "罗伯特\n默顿"}]},
    {**ZH, "thumbnail": {"shot": "s01"}},
    {**ZH, "thumbnail": {"shot": "s01", "text": "一\n二\n三\n四"}},
    {**ZH, "thumbnail": "s01"},
    {**ZH, "extra": 1},
])
def test_parse_publish_rejects_bad_input(raw):
    _errors(raw)


def test_draft_authors_come_from_the_front_matter_only():
    assert draft_authors(DRAFT) == frozenset({"罗伯特·默顿", "欧文·戈夫曼"})
    assert draft_authors("没有 front matter") == frozenset()


def test_chinese_description_lists_the_sources_after_the_body():
    text = description_text(parse_publish(ZH, SHOTS, "publish.json"), "zh")

    assert text.startswith(ZH["description"] + "\n\n本期出处\n")
    assert "· 罗伯特·默顿《社会理论与社会结构》（图书，1968）" in text
    assert "《自我实现的预言》一文收在此书中" in text
    assert "链接" not in text  # 还没有链接就不写


def test_english_description_includes_links_when_present():
    text = description_text(parse_publish(EN, SHOTS, "publish.en.json"), "en")

    assert "\n\nSources\n· Robert K. Merton, Social Theory and Social Structure (book)" in text
    assert "Link: https://example.com/book" in text


def test_descriptions_over_the_platform_limits_are_rejected():
    with pytest.raises(VideoError, match="1000"):
        description_text(parse_publish({**ZH, "description": "字" * 1000}, SHOTS, "p"), "zh")
    with pytest.raises(VideoError, match="5000"):
        description_text(parse_publish({**EN, "description": "é" * 2500}, SHOTS, "p"), "en")


def _setup(tmp_path, shots=None):
    work = WorkDir(tmp_path / "data" / "videos" / "v1")
    work.root.mkdir(parents=True)
    work.narration.write_text("第一句。\n第二句。\n", encoding="utf-8")
    shots = shots or [{"id": "s01", "line": 1, "bg": "蓝色", "scene": "站着"},
                      {"id": "s02", "line": 2, "bg": "红色", "scene": "走路"}]
    work.shots.write_text(json.dumps({"style": "风格{bg}：", "shots": shots}, ensure_ascii=False), encoding="utf-8")
    work.img_dir.mkdir()
    for shot in shots:
        work.image(shot["id"]).write_bytes(b"jpg")
    (tmp_path / "drafts").mkdir()
    draft = tmp_path / "drafts" / "v1.md"
    draft.write_text(DRAFT, encoding="utf-8")
    font = tmp_path / "font.ttf"
    font.write_bytes(b"ttf")
    return work, draft, font


def _write(path, raw):
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


def test_package_writes_title_description_and_both_chinese_covers(tmp_path):
    work, draft, font = _setup(tmp_path)
    _write(work.publish, ZH)
    runner = FakeRunner()

    outputs = package(work, font, draft, runner)

    assert work.title.read_text(encoding="utf-8") == ZH["title"] + "\n"
    assert "本期出处" in work.description.read_text(encoding="utf-8")
    assert outputs == (work.title, work.description, work.thumbnail("16x9"), work.thumbnail("3x4"))
    assert [c[-1] for c in runner.ffmpeg_calls() if c[-1].endswith(".jpg")] == [
        str(work.thumbnail("16x9")), str(work.thumbnail("3x4"))]
    assert work.thumbnail("16x9").name == "thumbnail.jpg" and work.thumbnail("3x4").name == "thumbnail-3x4.jpg"


def test_english_package_only_makes_the_wide_cover(tmp_path):
    work, draft, font = _setup(tmp_path)
    _write(work.publish, ZH)
    en = WorkDir(work.root, "en")
    _write(en.publish, EN)

    outputs = package(en, font, draft, FakeRunner())

    assert [p.name for p in outputs] == ["title.en.txt", "description.en.txt", "thumbnail.en.jpg"]


def test_sources_must_come_from_the_draft(tmp_path):
    work, draft, font = _setup(tmp_path)
    _write(work.publish, {**ZH, "sources": [{**ZH["sources"][0], "author": "卡尔·荣格"}]})

    with pytest.raises(VideoError, match="卡尔·荣格"):
        package(work, font, draft, FakeRunner())
    assert not work.title.exists()


def test_english_sources_must_match_the_chinese_ones(tmp_path):
    work, draft, font = _setup(tmp_path)
    en = WorkDir(work.root, "en")
    _write(en.publish, EN)
    with pytest.raises(VideoError, match="publish.json"):
        package(en, font, draft, FakeRunner())

    _write(work.publish, {**ZH, "sources": ZH["sources"] * 2})
    with pytest.raises(VideoError, match="2"):
        package(en, font, draft, FakeRunner())


def test_package_refuses_missing_inputs(tmp_path):
    work, draft, font = _setup(tmp_path)
    with pytest.raises(VideoError, match="publish.json"):
        package(work, font, draft, FakeRunner())

    _write(work.publish, ZH)
    with pytest.raises(VideoError, match="字体"):
        package(work, tmp_path / "missing.ttf", draft, FakeRunner())
    with pytest.raises(VideoError, match="草稿"):
        package(work, font, tmp_path / "drafts" / "missing.md", FakeRunner())
    work.publish.write_text("{", encoding="utf-8")
    with pytest.raises(VideoError, match="JSON"):
        package(work, font, draft, FakeRunner())


def test_unreadable_publish_file_is_a_video_error(tmp_path):
    work, draft, font = _setup(tmp_path)
    work.publish.write_bytes(b"\xff\xfe{")
    with pytest.raises(VideoError, match="UTF-8"):
        package(work, font, draft, FakeRunner())
    _write(work.publish, ZH)
    draft.write_bytes(b"---\n\xff\n---\n")
    with pytest.raises(VideoError, match="草稿"):
        package(work, font, draft, FakeRunner())
    work.publish.unlink()
    work.publish.mkdir()
    with pytest.raises(VideoError, match="读不了"):
        package(work, font, draft, FakeRunner())


def test_failed_cover_leaves_no_half_written_package(tmp_path):
    work, draft, font = _setup(tmp_path)
    _write(work.publish, ZH)

    with pytest.raises(VideoError):
        package(work, font, draft, FakeRunner(fail_on="-filter_complex"))

    assert not work.title.exists() and not work.description.exists()
    assert not work.package_tmp.exists()


def test_package_cover_needs_a_whole_frame_image(tmp_path):
    shots = [{"id": "s01", "line": 1, "bg": "蓝色", "scene": "站着",
              "motion": {"type": "rise", "at": [0.5, 0.5], "layers": {"bg": "桌子", "puff": "热气"}}},
             {"id": "s02", "line": 2, "bg": "红色", "scene": "走路"}]
    work, draft, font = _setup(tmp_path, shots)
    _write(work.publish, ZH)
    with pytest.raises(VideoError, match="分层"):
        package(work, font, draft, FakeRunner())

    _write(work.publish, {**ZH, "thumbnail": {"shot": "s02", "text": "好"}})
    work.image("s02").unlink()
    with pytest.raises(VideoError, match="s02"):
        package(work, font, draft, FakeRunner())


def test_cli_package(tmp_path, monkeypatch, capsys):
    work, _, _ = _setup(tmp_path)
    _write(work.publish, ZH)
    (tmp_path / "data" / "fonts").mkdir()
    (tmp_path / "data" / "fonts" / "SmileySans-Oblique.ttf").write_bytes(b"ttf")
    monkeypatch.chdir(tmp_path)
    runner = FakeRunner()
    monkeypatch.setattr(cli, "_services", lambda: cli.pipeline.Services(runner=runner))

    assert cli.main(["package", "data/videos/v1"]) == 0
    assert "thumbnail-3x4.jpg" in capsys.readouterr().out
    assert cli.main(["package", "data/videos/v1", "--draft", "../elsewhere.md"]) == 1
    assert cli.main(["package", "data/videos/v1", "--lang", "en"]) == 1  # 还没有 publish.en.json
