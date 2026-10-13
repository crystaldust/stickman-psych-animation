import pytest

from bili_collect.subtitles import parse_srt, pick_subtitle
from bili_collect.transcribe import Segment

SRT = """1
00:00:00,000 --> 00:00:02,500
不要太急着去谈恋爱

2
00:00:02,500 --> 00:01:05,040
也别因此感到慌张
第二行

"""


def test_parse_srt_reads_blocks_and_joins_multiline_text():
    assert parse_srt(SRT) == (
        Segment(0.0, 2.5, "不要太急着去谈恋爱"),
        Segment(2.5, 65.04, "也别因此感到慌张第二行"),
    )


def test_parse_srt_tolerates_crlf_and_skips_malformed_blocks():
    text = "1\r\n00:00:01,000 --> 00:00:02,000\r\n有效\r\n\r\ngarbage\r\n\r\n3\r\nnot a time\r\n文本\r\n"

    assert parse_srt(text) == (Segment(1.0, 2.0, "有效"),)


def test_parse_srt_handles_bom_and_missing_hours():
    text = "\ufeff00:01,000 --> 00:02,500\n短格式\n"

    assert parse_srt(text) == (Segment(1.0, 2.5, "短格式"),)


def test_parse_srt_skips_absurd_timestamps_without_crashing():
    text = "1\n" + "9" * 5000 + ":00:00,000 --> 00:00:01,000\nx\n\n2\n00:00:01,000 --> 00:00:02,000\n好\n"

    assert parse_srt(text) == (Segment(1.0, 2.0, "好"),)


def test_parse_srt_keeps_space_between_latin_lines():
    text = "1\n00:00:00,000 --> 00:00:01,000\nHello\nworld\n"

    assert parse_srt(text)[0].text == "Hello world"


def test_parse_srt_empty():
    assert parse_srt("") == ()


def sub(data):
    return [{"ext": "srt", "data": data}]


def test_pick_subtitle_prefers_human_chinese_over_ai():
    subtitles = {"danmaku": [{"ext": "xml", "url": "x"}], "ai-zh": sub("ai"), "zh-CN": sub("human")}

    assert pick_subtitle(subtitles) == ("zh-CN", "human")


def test_pick_subtitle_falls_back_to_ai_chinese():
    assert pick_subtitle({"danmaku": [], "ai-zh": sub("ai")}) == ("ai-zh", "ai")


@pytest.mark.parametrize(
    "subtitles",
    [{}, None, {"danmaku": [{"ext": "xml", "url": "x"}]}, {"en-US": sub("english")}, {"zh-CN": [{"ext": "srt"}]}],
)
def test_pick_subtitle_none_when_no_usable_chinese(subtitles):
    assert pick_subtitle(subtitles) is None
