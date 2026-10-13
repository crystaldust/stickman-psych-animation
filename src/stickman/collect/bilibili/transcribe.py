"""调用 mlx-whisper 转写音频，并输出 txt / srt / json 文案及来源信息。"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .storage import write_json, write_text

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
CHINESE_PROMPT = "以下是普通话的句子，使用简体中文，并带有标点符号。"
# Whisper 常见的人名、术语同音错写（实测加热词提示词会导致整段漏识别，所以改用确定性纠错）。
# 只收录「本身不是正常词语」的错写，避免误改（如不收「有意」→「友谊」）。
CORRECTIONS = tuple(
    (re.compile(pattern), right)
    for pattern, right in (
        ("[奇骑]泽克", "齐泽克"),
        ("舒[本德]华", "叔本华"),
        ("[科克]尔凯估尔", "克尔凯郭尔"),
        ("加谬(?![误论])", "加缪"),  # 不改「更加谬误」「增加谬论」
        ("一臂九鲁", "伊壁鸠鲁"),
        ("弗洛依德", "弗洛伊德"),
        ("崇高课体", "崇高客体"),
        ("思维反处", "思维反刍"),
        ("论字链", "论自恋"),
    )
)
# Whisper 在片尾纯音乐段常见的训练数据残留
HALLUCINATION_PATTERNS = re.compile(
    r"请不吝点赞[\s，。、,.]*订阅[\s，。、,.]*转发[\s，。、,.]*打赏支持明镜与点点栏目"
    r"|字幕由\s*Amara\.org\s*社区提供"
    r"|由\s*Amara\.org\s*社区提供的字幕"
)
SENTENCE_ENDINGS = ("。", "！", "？", "；", "…", "!", "?", ";")
CLOSING_MARKS = "”’」』）)\"'"
FULL_WIDTH = {",": "，", "?": "？", "!": "！", ";": "；", ":": "："}
HALF_WIDTH_AFTER_CJK = re.compile(r"([一-鿿])([,?!;:])")

Engine = Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


def _default_engine() -> Engine:
    import mlx_whisper  # 延迟导入：加载 mlx 很慢，只在真正转写时才需要

    return mlx_whisper.transcribe


def transcribe_audio(
    path: Path,
    engine: Engine | None = None,
    model: str = DEFAULT_MODEL,
) -> tuple[Segment, ...]:
    if not path.exists():
        raise FileNotFoundError(path)
    run = engine or _default_engine()
    result = run(
        str(path),
        path_or_hf_repo=model,
        language="zh",
        initial_prompt=CHINESE_PROMPT,
        word_timestamps=True,
        verbose=None,
    )
    sentences = (
        sentence
        for raw in result.get("segments", [])
        for sentence in _split_sentences(raw)
    )
    cleaned = (Segment(s.start, s.end, apply_corrections(remove_hallucinations(s.text))) for s in sentences)
    return tuple(s for s in cleaned if s.text)


def remove_hallucinations(text: str) -> str:
    return HALLUCINATION_PATTERNS.sub("", text).strip()


def apply_corrections(text: str) -> str:
    for pattern, right in CORRECTIONS:
        text = pattern.sub(right, text)
    return text


def normalize_punctuation(text: str) -> str:
    """Whisper 常在中文里输出半角标点，紧跟中文字符时换成全角。"""
    return HALF_WIDTH_AFTER_CJK.sub(lambda m: m.group(1) + FULL_WIDTH[m.group(2)], text)


def _sentence(words: list[dict[str, Any]]) -> Segment:
    text = normalize_punctuation("".join(str(w["word"]) for w in words).strip())
    return Segment(float(words[0]["start"]), float(words[-1]["end"]), text)


def _split_sentences(raw: dict[str, Any]) -> list[Segment]:
    """Whisper 的一段常有二三十秒，按逐词时间戳在句末标点处切成一句一段。"""
    words = raw.get("words") or []
    if not words:
        text = normalize_punctuation(str(raw["text"]))
        return [Segment(float(raw["start"]), float(raw["end"]), text)]

    sentences: list[Segment] = []
    pending: list[dict[str, Any]] = []
    for word in words:
        pending.append(word)
        if str(word["word"]).rstrip().rstrip(CLOSING_MARKS).endswith(SENTENCE_ENDINGS):
            sentences.append(_sentence(pending))
            pending = []
    if pending:
        sentences.append(_sentence(pending))
    return sentences


def _visible(segments: Iterable[Segment]) -> list[Segment]:
    return [Segment(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]


def format_srt_time(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, ms = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def format_srt(segments: Iterable[Segment]) -> str:
    blocks = [
        f"{i}\n{format_srt_time(s.start)} --> {format_srt_time(s.end)}\n{s.text}\n"
        for i, s in enumerate(_visible(segments), start=1)
    ]
    return "\n".join(blocks)


def format_txt(segments: Iterable[Segment]) -> str:
    lines = []
    for s in _visible(segments):
        minutes, secs = divmod(int(s.start), 60)
        lines.append(f"[{minutes:02d}:{secs:02d}] {s.text}\n")
    return "".join(lines)


def write_transcript(directory: Path, segments: Iterable[Segment], *, source: str) -> tuple[Path, ...]:
    """写出 txt / srt / json 三种格式，以及记录来源的 transcript.info.json。

    transcript.json 是「已完成」标记，所以最后写；中途失败时下次会重新生成。
    """
    visible = _visible(segments)
    txt, srt, info, data = (directory / f"transcript.{ext}" for ext in ("txt", "srt", "info.json", "json"))
    write_text(txt, format_txt(visible))
    write_text(srt, format_srt(visible))
    write_json(info, {"source": source, "segments": len(visible)})
    write_json(data, [{"start": s.start, "end": s.end, "text": s.text} for s in visible])
    return txt, srt, info, data
