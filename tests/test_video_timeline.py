import pytest
from video_fakes import word_meta

from stickman.video.errors import VideoError
from stickman.video.lang import ZH
from stickman.video.subtitles import SUB_STYLE, ass_document, ts
from stickman.video.text import apply_homophones, norm, split_sub, spoken
from stickman.video.timeline import (
    char_times,
    clip_frames,
    hold_events,
    keep_intervals,
    pause_cap,
    remap,
    shot_events,
)


def test_spoken_drops_corner_quotes_and_swaps_double_quotes():
    assert spoken("叫做「结晶」，他说“好”") == "叫做结晶，他说‘好’"


def test_homophones_apply_to_voice_only():
    assert spoken("想起ta，倒放") == "想起他，到放"
    assert apply_homophones("ta") == "他"
    assert split_sub("想起ta那天穿的外套，") == ["想起ta那天穿的外套"]


def test_norm_counts_one_char_per_timestamped_word():
    assert norm("「ta」到底喜不喜欢我？") == "他到底喜不喜欢我"
    assert len(norm("想起ta那天")) == len(norm("想起他那天"))


def test_split_sub_breaks_on_punctuation_and_long_runs():
    assert split_sub("奇怪的是，真正谈过的那几段，") == ["奇怪的是", "真正谈过的那几段"]
    assert split_sub("一" * 20) == ["一" * 16, "一" * 4]
    assert split_sub("「我一个人可以」，") == ["「我一个人可以」"]


def test_split_sub_never_cuts_a_book_title():
    # 回归：书名号的后半个被切到下一条字幕里。
    assert split_sub("一九二一年，弗洛伊德在《群体心理学与自我分析》里写道：") == [
        "一九二一年", "弗洛伊德在", "《群体心理学与自我分析》里写道："]
    long_title = "在《" + "一" * 20 + "》里"  # 书名本身超长才拆，拆完不丢字
    assert "".join(split_sub(long_title)) == long_title and max(map(len, split_sub(long_title))) <= 16


def test_split_sub_keeps_closing_quotes_with_their_line():
    # 回归：句末的后引号单独成了一条字幕，或者跑到下一条的开头。
    assert split_sub("“在爱中，自我变得贫乏，对象变得崇高。”") == ["“在爱中", "自我变得贫乏", "对象变得崇高”"]
    assert split_sub("后面一句是：“何事秋风悲画扇。”") == ["后面一句是：", "“何事秋风悲画扇”"]
    assert split_sub("他说：“好。”然后走了。") == ["他说：", "“好”", "然后走了"]
    assert split_sub("一" * 15 + "“好”") == ["一" * 15, "“好”"]
    assert split_sub("他说：「她说“好。”」然后") == ["他说：", "「她说“好”」", "然后"]  # 叠在一起的后引号都跟着前一条
    assert split_sub("（他说：“好。”）然后走") == ["（他说：", "“好”）", "然后走"]


def test_char_times_maps_each_char_to_its_word():
    meta = word_meta("你好，世界。")

    times, tokens = char_times(meta, norm("你好，世界。"), "s01", ZH)

    assert len(times) == 4
    assert times[0] == (0.1, 0.3)
    assert len(tokens) == 6


def test_char_times_accepts_original_word_returned_instead_of_homophone():
    meta = {"subtitle": {"sentences": [{"words": [
        {"text": "倒放", "start_time": 0, "end_time": 400},
        {"text": "。", "start_time": 400, "end_time": 400},
    ]}]}}

    times, _ = char_times(meta, norm("倒放。"), "s01", ZH)

    assert times == ((0.0, 0.4), (0.0, 0.4))


def test_char_times_rejects_stale_voice_naming_the_shot():
    with pytest.raises(VideoError) as exc:
        char_times(word_meta("旧的旁白"), norm("新的旁白"), "voice/s03.mp3", ZH)

    assert "s03" in str(exc.value)


@pytest.mark.parametrize("meta", [{}, {"subtitle": {"sentences": [{"words": [{"text": "a"}]}]}}, {"subtitle": []}])
def test_char_times_rejects_malformed_metadata(meta):
    with pytest.raises(VideoError):
        char_times(meta, "a", "s01", ZH)


@pytest.mark.parametrize("mark, cap", [("。", 0.6), ("？", 0.6), ("，", 0.35), ("", 0.35), ("、", 0.35)])
def test_pause_caps(mark, cap):
    assert pause_cap(mark) == cap


def test_keep_intervals_shortens_long_pauses_only():
    # 你好（0.1–0.5）。停 2 秒 世界（2.5–2.9）；短停顿不动
    meta = word_meta("你好。世界", pauses={2: 2000})
    _, tokens = char_times(meta, norm("你好。世界"), "s01", ZH)

    spans = keep_intervals(tokens, total=3.5, lang=ZH)

    assert spans == ((0.05, pytest.approx(0.8)), (pytest.approx(2.2), pytest.approx(3.05)))
    kept = sum(e - s for s, e in spans)
    assert kept == pytest.approx(0.75 + 0.85)


def test_keep_intervals_without_words_is_an_error():
    with pytest.raises(VideoError):
        keep_intervals((), 1.0, ZH)


def test_remap_is_monotonic_and_skips_cut_spans():
    spans = ((0.0, 1.0), (3.0, 4.0))

    values = [remap(t, spans) for t in (0.0, 0.5, 1.0, 2.0, 3.0, 3.5, 4.0, 9.0)]

    assert values == [0.0, 0.5, 1.0, 1.0, 1.0, 1.5, 2.0, 2.0]


def test_shot_events_follow_subtitle_chunks_with_offset():
    lines = ("你好，世界。",)
    times = ((0.0, 0.1), (0.1, 0.2), (0.3, 0.4), (0.4, 0.5))

    events = shot_events(lines, times, offset=10.0, lang=ZH)

    assert events == ((10.0, 10.2, "你好"), (10.3, 10.5, "世界"))


def test_shot_events_skip_chunks_without_spoken_chars():
    assert shot_events(("……", "好。"), ((0.0, 0.2),), offset=0.0, lang=ZH) == ((0.0, 0.2, "好"),)


def test_hold_events_bridges_short_gaps_and_extends_long_ones_without_mutating():
    events = ((0.0, 1.0, "a"), (1.2, 2.0, "b"), (5.0, 6.0, "c"))

    held = hold_events(events)

    assert held == ((0.0, 1.2, "a"), (1.2, 2.3, "b"), (5.0, 6.5, "c"))
    assert events[0] == (0.0, 1.0, "a")


def test_clip_frames_cut_halfway_into_the_shot_gap():
    assert clip_frames((0.4, 3.0, 6.0), total=9.0) == (0, 82, 172, 270)


def test_ts_and_ass_document():
    assert ts(3725.456) == "1:02:05.46"
    doc = ass_document(((0.0, 1.5, "你好"),))
    assert SUB_STYLE in doc
    assert doc.endswith("Dialogue: 0,0:00:00.00,0:00:01.50,Default,你好\n")


def test_ta_homophone_leaves_english_words_alone():
    assert spoken("the data is a start") == "the data is a start"
    assert spoken("想起ta，ta说") == "想起他，他说"


@pytest.mark.parametrize("sec, text", [(59.996, "0:01:00.00"), (3599.999, "1:00:00.00"), (0.004, "0:00:00.00")])
def test_ts_never_prints_sixty_seconds(sec, text):
    assert ts(sec) == text
