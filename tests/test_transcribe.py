import json

import pytest

from bili_collect.transcribe import (
    DEFAULT_MODEL,
    Segment,
    format_srt,
    format_srt_time,
    format_txt,
    transcribe_audio,
    write_transcript,
)


@pytest.mark.parametrize(
    "seconds, expected",
    [(0, "00:00:00,000"), (7.5, "00:00:07,500"), (3661.001, "01:01:01,001"), (59.9996, "00:01:00,000")],
)
def test_format_srt_time(seconds, expected):
    assert format_srt_time(seconds) == expected


SEGMENTS = (Segment(0.0, 2.5, " 不要太急着去谈恋爱 "), Segment(2.5, 5.0, "也别因此感到慌张"))


def test_format_srt():
    assert format_srt(SEGMENTS) == (
        "1\n00:00:00,000 --> 00:00:02,500\n不要太急着去谈恋爱\n\n"
        "2\n00:00:02,500 --> 00:00:05,000\n也别因此感到慌张\n"
    )


def test_format_txt_prefixes_timestamps():
    assert format_txt(SEGMENTS) == "[00:00] 不要太急着去谈恋爱\n[00:02] 也别因此感到慌张\n"


def test_format_skips_blank_segments():
    segs = (Segment(0, 1, "  "), Segment(1, 2, "有内容"))

    assert format_txt(segs) == "[00:01] 有内容\n"
    assert format_srt(segs).startswith("1\n00:00:01,000")


def test_transcribe_audio_calls_engine_with_chinese_defaults(tmp_path):
    calls = []

    def engine(path, **kwargs):
        calls.append((path, kwargs))
        return {"segments": [{"start": 0, "end": 1.2, "text": "你好"}], "text": "你好"}

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"x")

    segments = transcribe_audio(audio, engine=engine)

    assert segments == (Segment(0.0, 1.2, "你好"),)
    path, kwargs = calls[0]
    assert path == str(audio)
    assert kwargs["path_or_hf_repo"] == DEFAULT_MODEL
    assert kwargs["language"] == "zh"
    assert "简体" in kwargs["initial_prompt"]


def words(*items):
    return [{"word": w, "start": s, "end": e} for w, s, e in items]


def test_transcribe_audio_splits_segments_into_sentences_by_word_timestamps(tmp_path):
    raw = {
        "segments": [
            {
                "start": 0.0,
                "end": 4.0,
                "text": "不要着急。也别慌张,好吗?",
                "words": words(("不要", 0.0, 0.3), ("着急。", 0.3, 1.0), ("也别", 1.4, 1.8), ("慌张,", 1.8, 2.5), ("好吗?", 2.6, 3.2)),
            },
            {"start": 4.0, "end": 5.0, "text": "没有逐词时间戳", "words": []},
        ]
    }
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"x")
    calls = []

    def engine(path, **kwargs):
        calls.append(kwargs)
        return raw

    segments = transcribe_audio(audio, engine=engine)

    assert calls[0]["word_timestamps"] is True
    assert segments == (
        Segment(0.0, 1.0, "不要着急。"),
        Segment(1.4, 3.2, "也别慌张，好吗？"),
        Segment(4.0, 5.0, "没有逐词时间戳"),
    )


def test_transcribe_audio_flushes_trailing_words_without_punctuation(tmp_path):
    raw = {"segments": [{"start": 0, "end": 2, "text": "", "words": words(("最后", 0.0, 0.5), ("一句", 0.5, 1.1))}]}
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"x")

    assert transcribe_audio(audio, engine=lambda p, **k: raw) == (Segment(0.0, 1.1, "最后一句"),)


def test_split_handles_closing_quotes(tmp_path):
    raw = {
        "segments": [
            {
                "start": 0,
                "end": 3,
                "text": "",
                "words": words(("他说", 0.0, 0.4), ("“算了。”", 0.4, 1.0), ("然后", 1.2, 1.6), ("走了！」", 1.6, 2.0)),
            }
        ]
    }
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"x")

    assert transcribe_audio(audio, engine=lambda p, **k: raw) == (
        Segment(0.0, 1.0, "他说“算了。”"),
        Segment(1.2, 2.0, "然后走了！」"),
    )


def test_transcribe_audio_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        transcribe_audio(tmp_path / "missing.wav", engine=lambda *a, **k: {})


def test_write_transcript_outputs_three_formats(tmp_path):
    paths = write_transcript(tmp_path, SEGMENTS)

    assert sorted(p.name for p in paths) == ["transcript.json", "transcript.srt", "transcript.txt"]
    data = json.loads((tmp_path / "transcript.json").read_text(encoding="utf-8"))
    assert data == [
        {"start": 0.0, "end": 2.5, "text": "不要太急着去谈恋爱"},
        {"start": 2.5, "end": 5.0, "text": "也别因此感到慌张"},
    ]
