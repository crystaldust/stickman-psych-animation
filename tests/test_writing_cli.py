import json

import pytest

from stickman import cli as root_cli
from stickman.writing import cli

from writing_fixtures import make_video, valid_analysis


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    make_video(tmp_path / "data", "BV1aaaaaaaaa", "缺爱", [(0.0, 3.0, "真正的平静从来不是身边终于没有了麻烦。")])
    return tmp_path


def test_corpus_show_and_list(project, capsys):
    assert root_cli.main(["corpus", "show", "BV1aaaaaaaaa"]) == 0
    assert "真正的平静" in capsys.readouterr().out

    assert root_cli.main(["corpus", "list"]) == 0
    assert "BV1aaaaaaaaa" in capsys.readouterr().out


def test_corpus_export_to_file(project):
    assert cli.corpus_main(["export", "--output", "out/corpus.md"]) == 0

    assert "缺爱" in (project / "out" / "corpus.md").read_text(encoding="utf-8")


def test_corpus_show_unknown_video(project, capsys):
    assert cli.corpus_main(["show", "BV1zzzzzzzzz"]) == 1
    assert "找不到" in capsys.readouterr().err


def test_analysis_save_from_file_and_show(project, capsys):
    src = project / "a.json"
    src.write_text(json.dumps(valid_analysis(), ensure_ascii=False), encoding="utf-8")

    assert root_cli.main(["analysis", "save", str(src)]) == 0
    assert (project / "analysis" / "BV1aaaaaaaaa.json").exists()

    assert root_cli.main(["analysis", "show", "BV1aaaaaaaaa"]) == 0
    assert "误引" in capsys.readouterr().out


def test_analysis_save_from_stdin(project, monkeypatch):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(valid_analysis())))

    assert cli.analysis_main(["save", "-"]) == 0


def test_analysis_save_rejects_invalid(project, capsys):
    bad = valid_analysis()
    bad["claims"][0]["verdict"] = "maybe"
    src = project / "bad.json"
    src.write_text(json.dumps(bad), encoding="utf-8")

    assert cli.analysis_main(["save", str(src)]) == 1
    assert "verdict" in capsys.readouterr().err
    assert not (project / "analysis").exists()


def test_analysis_save_rejects_malformed_json(project, capsys):
    src = project / "bad.json"
    src.write_text("{oops", encoding="utf-8")

    assert cli.analysis_main(["save", str(src)]) == 1
    assert "JSON" in capsys.readouterr().err


def test_library_build_and_show(project, capsys):
    (project / "analysis").mkdir()
    (project / "analysis" / "BV1aaaaaaaaa.json").write_text(json.dumps(valid_analysis()), encoding="utf-8")

    assert root_cli.main(["library", "build"]) == 0
    assert (project / "library" / "sources.jsonl").exists()
    assert "部作品" in capsys.readouterr().out

    assert root_cli.main(["library", "show"]) == 0
    assert "论自恋" in capsys.readouterr().out


def test_draft_check_flags_copied_text(project, capsys):
    draft = project / "drafts" / "d.md"
    draft.parent.mkdir()
    draft.write_text("---\ntitle: x\n---\n真正的平静从来不是身边终于没有了麻烦，这是我的看法。", encoding="utf-8")

    assert root_cli.main(["draft", "check", str(draft), "--min-chars", "10"]) == 1
    assert "BV1aaaaaaaaa" in capsys.readouterr().out


def test_draft_check_passes_original_text(project, capsys):
    draft = project / "d.md"
    draft.write_text("平静是一种练习，你允许情绪经过，但不让它替你做决定。", encoding="utf-8")

    assert cli.draft_main(["check", str(draft)]) == 0
    out = capsys.readouterr().out
    assert "未发现" in out
    assert "正文 23 字" in out
    assert "偏短" in out  # 少于 650 字只提示，不算失败


def test_draft_check_with_empty_corpus_is_not_a_pass(project, capsys):
    draft = project / "d.md"
    draft.write_text("随便写点什么。", encoding="utf-8")

    assert cli.draft_main(["--data-dir", "nowhere", "check", str(draft)]) == 2
    assert "没有已采集的文案" in capsys.readouterr().err


def test_draft_check_min_chars_must_be_positive(project):
    with pytest.raises(SystemExit):
        cli.draft_main(["check", "d.md", "--min-chars", "0"])


def test_draft_check_missing_file(project, capsys):
    assert cli.draft_main(["check", "nope.md"]) == 1
    assert "读不到草稿" in capsys.readouterr().err


def test_library_show_without_build_hints_and_fails(project, capsys):
    assert cli.library_main(["show"]) == 1
    assert "library build" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["corpus", "export", "--output", "/tmp/elsewhere.md"],
        ["corpus", "export", "--output", "../outside.md"],
        ["analysis", "--analysis-dir", "/tmp/x", "list"],
        ["library", "--library", "/tmp/x.jsonl", "build"],
    ],
)
def test_output_paths_must_stay_inside_project(project, capsys, argv):
    assert root_cli.main(argv) == 1
    assert "项目目录" in capsys.readouterr().err


def test_corpus_list_survives_a_corrupt_video(project, capsys):
    broken = make_video(project / "data", "BV1bbbbbbbbb", "坏", [(0.0, 1.0, "x")])
    (broken / "transcript.json").write_text("{oops", encoding="utf-8")

    assert cli.corpus_main(["list"]) == 0
    assert "BV1aaaaaaaaa" in capsys.readouterr().out


def test_corpus_list_rejects_bad_mid(project, capsys):
    assert cli.corpus_main(["list", "--mid", "../.."]) == 1
