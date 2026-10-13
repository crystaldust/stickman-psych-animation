import json

import pytest

from stickman.writing.analysis import (
    AnalysisError,
    load_analyses,
    load_analysis,
    render_report,
    save_analysis,
    validate_analysis,
)

from writing_fixtures import valid_analysis


def test_valid_analysis_has_no_errors():
    assert validate_analysis(valid_analysis()) == []


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.pop("bvid"), "bvid"),
        (lambda d: d.update(bvid="../x"), "bvid"),
        (lambda d: d.update(summary=""), "summary"),
        (lambda d: d.update(themes="亲密关系"), "themes"),
        (lambda d: d["structure"][0].update(role="intro"), "role"),
        (lambda d: d["structure"][0].update(start=-1), "start"),
        (lambda d: d["claims"][0].update(verdict="maybe"), "verdict"),
        (lambda d: d["claims"][0].update(quote=""), "quote"),
        (lambda d: d["claims"][1].update(id="c1"), "重复"),
        (lambda d: d["claims"][0].update(source={"title": "x"}), "author"),
        (lambda d: d["claims"][0].update(source=None), "source"),  # 判定为原话却没有出处
        (lambda d: d["concepts"][0].update(name=""), "name"),
        (lambda d: d.update(extra=1), "extra"),
        (lambda d: d.update(structure=["x"]), "structure[0] 必须是对象"),
        (lambda d: d.update(concepts=["x"]), "concepts[0] 必须是对象"),
        (lambda d: d.update(claims="x"), "claims 必须是列表"),
        (lambda d: d.update(concepts=None), "concepts 必须是列表"),
        (lambda d: d.update(structure=[]), "structure 必须是非空列表"),
        (lambda d: d["claims"][0].update(id=["a"]), "claims[0].id"),
        (lambda d: d["structure"][0].update(start=True), "structure[0].start"),
        (lambda d: d["claims"][0].update(source="x"), "claims[0].source 必须是对象"),
        (lambda d: d["claims"][0]["source"].update(url="javascript:alert(1)"), "claims[0].source.url"),
        (lambda d: d["claims"][0]["source"].update(url="ftp://x.org/a"), "claims[0].source.url"),
        (lambda d: d["claims"][0].update(notes="正常文字\u200b藏了零宽字符"), "claims[0].notes"),
        (lambda d: d["claims"][0].update(original="a" * 3000), "claims[0].original"),
        (lambda d: d["claims"][1].update(verdict="paraphrase", source=None), "claims[1].source 必填"),
    ],
)
def test_invalid_analysis_reports_field(mutate, message):
    data = valid_analysis()
    mutate(data)

    errors = validate_analysis(data)

    assert errors and any(message in e for e in errors), errors


def test_all_errors_are_reported_together():
    data = valid_analysis()
    data["summary"] = ""
    data["claims"][0]["verdict"] = "maybe"

    errors = validate_analysis(data)

    assert any("summary" in e for e in errors)
    assert any("claims[0].verdict" in e for e in errors)


def test_newlines_in_notes_are_allowed():
    data = valid_analysis()
    data["claims"][0]["notes"] = "第一行\n第二行"

    assert validate_analysis(data) == []


def test_unverified_claim_may_have_no_source():
    data = valid_analysis()
    data["claims"][0].update(verdict="unverified", source=None, original=None)

    assert validate_analysis(data) == []


def test_non_dict_is_rejected():
    assert validate_analysis(["not", "a", "dict"])


def test_save_and_load_round_trip(tmp_path):
    path = save_analysis(valid_analysis(), tmp_path)

    assert path == tmp_path / "BV1aaaaaaaaa.json"
    assert load_analysis(tmp_path, "BV1aaaaaaaaa")["summary"].startswith("用弗洛伊德")
    assert [a["bvid"] for a in load_analyses(tmp_path)] == ["BV1aaaaaaaaa"]


def test_save_refuses_invalid_data_and_writes_nothing(tmp_path):
    data = valid_analysis()
    data["claims"][0]["verdict"] = "maybe"

    with pytest.raises(AnalysisError) as excinfo:
        save_analysis(data, tmp_path)

    assert "verdict" in str(excinfo.value)
    assert not list(tmp_path.iterdir())


def test_load_analyses_reports_corrupt_file(tmp_path):
    (tmp_path / "BV1bad000000.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(AnalysisError, match="BV1bad000000"):
        load_analyses(tmp_path)


def test_load_missing_analysis(tmp_path):
    with pytest.raises(AnalysisError, match="没有分析结果"):
        load_analysis(tmp_path, "BV1aaaaaaaaa")


def test_render_report_lists_claims_with_verdicts():
    text = render_report(valid_analysis())

    assert "为什么缺爱的人谁陪他就爱谁" in text
    assert "原话" in text and "误引" in text
    assert "论自恋" in text
    assert "荣格" in text
