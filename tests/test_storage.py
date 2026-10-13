import json

import pytest

from bili_collect.models import VideoEntry
from bili_collect.storage import Layout, merge_entries, read_index, write_index, write_json


def entry(bvid, title="t", **kw):
    return VideoEntry(bvid=bvid, title=title, **kw)


def test_layout_paths(tmp_path):
    layout = Layout(tmp_path)

    assert layout.user_dir("42") == tmp_path / "42"
    assert layout.index_path("42") == tmp_path / "42" / "videos.jsonl"
    assert layout.video_dir("42", "BV1CSWXzAEys") == tmp_path / "42" / "BV1CSWXzAEys"


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "BV1/../../etc"])
def test_layout_rejects_path_traversal(tmp_path, bad):
    layout = Layout(tmp_path)

    with pytest.raises(ValueError):
        layout.video_dir("42", bad)
    with pytest.raises(ValueError):
        layout.user_dir(bad)


def test_merge_entries_dedupes_by_bvid_and_prefers_newer_non_empty_fields():
    old = [entry("BV1", "old", view_count=10), entry("BV2", "two")]
    new = [entry("BV1", "new", view_count=None), entry("BV3", "three")]

    merged = merge_entries(old, new)

    assert [e.bvid for e in merged] == ["BV1", "BV2", "BV3"]
    assert merged[0].title == "new"
    assert merged[0].view_count == 10
    assert old[0].title == "old"


def test_index_round_trip(tmp_path):
    path = tmp_path / "u" / "videos.jsonl"
    entries = [entry("BV1", "标题", duration=61.5, upload_date="20251021")]

    write_index(path, entries)

    assert read_index(path) == entries
    assert "标题" in path.read_text(encoding="utf-8")


def test_read_index_missing_file_returns_empty(tmp_path):
    assert read_index(tmp_path / "nope.jsonl") == []


def test_read_index_reports_corrupt_line(tmp_path):
    path = tmp_path / "videos.jsonl"
    path.write_text('{"bvid": "BV1", "title": "a"}\nnot json\n', encoding="utf-8")

    with pytest.raises(ValueError, match="line 2"):
        read_index(path)


def test_write_json_is_atomic_and_utf8(tmp_path):
    path = tmp_path / "a" / "meta.json"

    write_json(path, {"title": "你好"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"title": "你好"}
    assert not list(path.parent.glob("*.tmp"))


def test_written_files_are_world_readable(tmp_path):
    path = tmp_path / "meta.json"

    write_json(path, {})

    assert path.stat().st_mode & 0o777 == 0o644
