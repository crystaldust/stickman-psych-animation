import pytest

from stickman.writing.library import LibraryError, build_library, read_library, render_library, write_library

from writing_fixtures import valid_analysis


def test_build_library_groups_by_work_and_tracks_usage():
    second = valid_analysis("BV1bbbbbbbbb")
    second["claims"] = second["claims"][:1]

    entries = build_library([valid_analysis(), second])

    freud = next(e for e in entries if "自恋" in e["title"])
    assert freud["author"] == "西格蒙德·弗洛伊德"
    assert freud["used_in"] == ["BV1aaaaaaaaa", "BV1bbbbbbbbb"]
    kinds = sorted(i["kind"] for i in freud["items"])
    assert kinds == ["concept", "quote", "quote"]


def test_titles_are_normalised_when_grouping():
    a = valid_analysis()
    a["claims"][0]["source"]["title"] = "《论自恋：一篇导论》 "

    entries = build_library([a])

    assert sum(1 for e in entries if "自恋" in e["title"]) == 1


def test_parenthetical_aliases_do_not_split_a_work():
    a = valid_analysis()
    a["claims"][0]["source"]["title"] = "论自恋：一篇导论（On Narcissism, 1914）"
    a["concepts"][0]["source"]["title"] = "论自恋：一篇导论(On Narcissism)"

    entries = build_library([a])

    freud = [e for e in entries if "自恋" in e["title"]]
    assert len(freud) == 1
    assert freud[0]["title"] == "论自恋：一篇导论"


def test_misattributed_claims_are_kept_but_flagged():
    entries = build_library([valid_analysis()])

    jung = next(e for e in entries if e["author"] == "卡尔·荣格")
    assert jung["items"][0]["verdict"] == "misattributed"


def test_claims_without_source_are_skipped():
    a = valid_analysis()
    a["claims"] = [dict(a["claims"][1], verdict="unverified", source=None)]
    a["concepts"] = []

    assert build_library([a]) == []


def test_library_round_trip_and_render(tmp_path):
    path = tmp_path / "library" / "sources.jsonl"
    entries = build_library([valid_analysis()])

    write_library(entries, path)

    assert read_library(path) == entries
    text = render_library(entries)
    assert "论自恋" in text and "误引" in text


def test_read_missing_library_is_an_error(tmp_path):
    with pytest.raises(LibraryError, match="library build"):
        read_library(tmp_path / "nope.jsonl")


def test_author_names_are_normalised_when_grouping():
    a = valid_analysis()
    a["claims"][0]["source"]["author"] = "西格蒙德 · 弗洛伊德"
    a["concepts"][0]["source"]["author"] = "西格蒙德・弗洛伊德"

    entries = build_library([a])

    assert sum(1 for e in entries if "自恋" in e["title"]) == 1


def test_title_that_is_only_an_alias_falls_back_to_raw():
    a = valid_analysis()
    a["claims"][0]["source"]["title"] = "(1930)"

    entries = build_library([a])

    assert any(e["title"] == "(1930)" for e in entries)


def test_corrupt_library_line_reports_line_number(tmp_path):
    path = tmp_path / "sources.jsonl"
    path.write_text('{"author": "a", "title": "b", "items": [], "used_in": []}\n{oops\n', encoding="utf-8")

    with pytest.raises(LibraryError, match="第 2 行"):
        read_library(path)
