import pytest

from stickman.writing.overlap import CHARS_PER_SECOND, draft_length, find_overlaps, strip_front_matter

CORPUS = {
    "BV1aaaaaaaaa": "真正的平静从来不是身边终于没有了麻烦，而是你终于确认，哪怕失去很多东西，你依然有能力把自己接住。",
    "BV1bbbbbbbbb": "人与人的悲欢并不相通。",
}


def test_copied_sentence_is_detected_even_with_changed_punctuation():
    draft = "我想说，真正的平静 从来不是身边终于没有了麻烦！而是别的东西。"

    overlaps = find_overlaps(draft, CORPUS, min_chars=12)

    assert len(overlaps) == 1
    assert overlaps[0].bvid == "BV1aaaaaaaaa"
    assert overlaps[0].text == "真正的平静从来不是身边终于没有了麻烦而是"


def test_original_text_has_no_overlap():
    draft = "平静是一种练习：你允许情绪经过，但不让它替你做决定。"

    assert find_overlaps(draft, CORPUS, min_chars=12) == []


def test_quoted_citations_are_ignored():
    draft = "鲁迅说过：“人与人的悲欢并不相通。”所以我们更需要倾听。"

    assert find_overlaps(draft, CORPUS, min_chars=8) == []


def test_short_common_phrases_below_threshold_are_ignored():
    draft = "哪怕失去很多东西，我们还是要往前走。"

    assert find_overlaps(draft, CORPUS, min_chars=12) == []


def test_front_matter_is_stripped():
    text = "---\ntitle: 测试\nsources: [a]\n---\n正文"

    assert strip_front_matter(text) == "正文"
    assert strip_front_matter("没有前言") == "没有前言"


def test_draft_length_counts_spoken_text_only():
    text = "---\ntitle: 很长的标题不算\n---\n第一句，有标点。\n> 画面：这一行不算字数\nHello world\n"

    chars, seconds = draft_length(text)

    assert chars == len("第一句有标点") + len("Helloworld")
    assert seconds == pytest.approx(chars / CHARS_PER_SECOND)


COPIED = "真正的平静从来不是身边终于没有了麻烦"


def test_two_separate_overlaps_are_reported_separately():
    draft = f"{COPIED}。中间是我自己写的很多内容，完全原创。哪怕失去很多东西你依然有能力把自己接住"

    assert len(find_overlaps(draft, CORPUS, min_chars=12)) == 2


def test_overlap_exactly_at_threshold_counts_and_one_below_does_not():
    assert find_overlaps("真正的平静从来不是身", CORPUS, min_chars=10)
    assert not find_overlaps("真正的平静从来不是", CORPUS, min_chars=10)


def test_long_quoted_text_is_still_checked():
    draft = f"他说“我不同意，{COPIED}，然后又补充了很多很多很多很多很多很多很多很多很多很多很多很多很多很多很多很多很多话”好"

    assert find_overlaps(draft, CORPUS, min_chars=12)


def test_unbalanced_quote_does_not_hide_following_text():
    draft = f"他说“我不同意。\n下一段：{COPIED}。"

    assert find_overlaps(draft, CORPUS, min_chars=12)


def test_ascii_double_quotes_are_not_treated_as_citations():
    draft = f'他说"a {COPIED} "b"'

    assert find_overlaps(draft, CORPUS, min_chars=12)


def test_pipe_character_in_draft_does_not_split_text():
    draft = "真正的平静从来不是|身边终于没有了麻烦"

    assert find_overlaps(draft, CORPUS, min_chars=12)


def test_min_chars_must_be_positive():
    with pytest.raises(ValueError):
        find_overlaps("x", CORPUS, min_chars=0)


def test_empty_inputs():
    assert find_overlaps("", CORPUS) == []
    assert find_overlaps(COPIED, {}) == []


@pytest.mark.parametrize(
    "text, expected",
    [
        ("---\r\ntitle: x\r\n---\r\n正文", "正文"),
        ("---\n---\n正文", "正文"),
        ("---\ntitle: x\n----\n还在前言里\n---\n正文", "正文"),
        ("---\n没有结束", "---\n没有结束"),
    ],
)
def test_front_matter_edge_cases(text, expected):
    assert strip_front_matter(text) == expected
