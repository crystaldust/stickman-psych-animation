import pytest

from stickman.writing.corpus import CorpusError, find_video, iter_videos, render_corpus, render_markdown

from writing_fixtures import MID, make_video


@pytest.fixture
def data_root(tmp_path):
    make_video(tmp_path, "BV1aaaaaaaaa", "旧视频", [(0.0, 2.0, "第一句。"), (2.0, 65.0, "第二句。")], upload_date="20260901")
    make_video(tmp_path, "BV1bbbbbbbbb", "新视频", [(0.0, 3.0, "新的一句。")], upload_date="20261005", view_count=99)
    (tmp_path / MID / "BV1ccccccccc").mkdir(parents=True)  # 没有文案的视频
    return tmp_path


def test_find_video_without_knowing_mid(data_root):
    record = find_video(data_root, "BV1aaaaaaaaa")

    assert record.mid == MID
    assert record.title == "旧视频"
    assert [s.text for s in record.segments] == ["第一句。", "第二句。"]
    assert record.bgm == ("been loving",)


def test_find_video_errors(data_root):
    with pytest.raises(CorpusError, match="找不到"):
        find_video(data_root, "BV1zzzzzzzzz")
    with pytest.raises(CorpusError, match="没有文案"):
        find_video(data_root, "BV1ccccccccc")
    with pytest.raises(CorpusError, match="BV"):
        find_video(data_root, "../etc")


def test_iter_videos_newest_first_and_skips_untranscribed(data_root):
    assert [r.bvid for r in iter_videos(data_root)] == ["BV1bbbbbbbbb", "BV1aaaaaaaaa"]
    assert iter_videos(data_root, mid="999") == []


def test_render_markdown_has_metadata_and_timestamped_lines(data_root):
    text = render_markdown(find_video(data_root, "BV1aaaaaaaaa"))

    assert text.startswith("## 旧视频")
    assert "BV1aaaaaaaaa" in text
    assert "2026-09-01" in text
    assert "been loving" in text
    assert "[00:00] 第一句。" in text
    assert "[01:05]" not in text and "[00:02] 第二句。" in text


def test_render_corpus_joins_records(data_root):
    text = render_corpus(iter_videos(data_root))

    assert text.index("新视频") < text.index("旧视频")
    assert "共 2 篇" in text


def test_corrupt_transcript_is_a_clean_error_for_show_and_skipped_when_listing(data_root, caplog):
    broken = make_video(data_root, "BV1ddddddddd", "坏数据", [(0.0, 1.0, "x")])
    (broken / "transcript.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(CorpusError, match="损坏"):
        find_video(data_root, "BV1ddddddddd")

    assert "BV1ddddddddd" not in [r.bvid for r in iter_videos(data_root)]
    assert "BV1ddddddddd" in caplog.text


@pytest.mark.parametrize("payload", ['[{"start": 0}]', '{"a": 1}', '["x"]'])
def test_malformed_segments_are_reported(data_root, payload):
    broken = make_video(data_root, "BV1eeeeeeeee", "坏数据", [(0.0, 1.0, "x")])
    (broken / "transcript.json").write_text(payload, encoding="utf-8")

    with pytest.raises(CorpusError, match="损坏"):
        find_video(data_root, "BV1eeeeeeeee")


def test_non_object_meta_is_reported(data_root):
    broken = make_video(data_root, "BV1fffffffff", "坏数据", [(0.0, 1.0, "x")])
    (broken / "meta.json").write_text("[1]", encoding="utf-8")

    with pytest.raises(CorpusError, match="损坏"):
        find_video(data_root, "BV1fffffffff")


def test_invalid_mid_is_rejected(data_root):
    with pytest.raises(CorpusError, match="mid"):
        iter_videos(data_root, mid="../..")
