"""配音时间线（纯函数，不碰文件）：词级时间戳 → 每个字的时间、要保留的片段、字幕事件、镜头切点。"""

from typing import Any

from .config import (
    COMMA_PAUSE,
    FPS,
    HEAD_KEEP,
    LAST_SUB_EXTRA,
    SENTENCE_PAUSE,
    SHOT_GAP,
    SUB_EXTRA,
    SUB_HOLD,
    TAIL_KEEP,
    WORD_PAUSE,
)
from .errors import VideoError
from .lang import Lang

Span = tuple[float, float]
Event = tuple[float, float, str]


def word_tokens(meta: Any, label: str) -> tuple[dict, ...]:
    try:
        tokens = tuple(w for s in meta["subtitle"]["sentences"] for w in s["words"])
    except (KeyError, TypeError) as exc:
        raise VideoError(f"{label}：配音元数据里没有词级时间戳") from exc
    for w in tokens:
        if not (isinstance(w, dict) and isinstance(w.get("text"), str)
                and type(w.get("start_time")) is int and type(w.get("end_time")) is int):
            raise VideoError(f"{label}：词级时间戳格式不对：{str(w)[:80]}")
    return tokens


def char_times(meta: Any, expected: str, label: str, lang: Lang) -> tuple[tuple[Span, ...], tuple[dict, ...]]:
    """每个字（英文是每个字母）的 (开始, 结束) 秒数，以及原始 token（含标点、空格）。

    返回的字幕有时写的是原词（倒放）而不是送去的同音字（到放），所以比较前先做一次替换。
    对不上说明配音是旧的（旁白改过），必须报错，否则字幕会整体错位。
    """
    tokens = word_tokens(meta, label)
    words = [w for w in tokens if lang.norm(w["text"])]
    got = lang.canon("".join(lang.norm(w["text"]) for w in words))
    if got != expected:
        raise VideoError(f"{label}：配音内容和旁白对不上（旁白改过的话，删掉这段配音重新生成）\n"
                         f"  旁白 {expected}\n  配音 {got}")
    times = tuple((w["start_time"] / 1000, w["end_time"] / 1000) for w in words for _ in lang.norm(w["text"]))
    return times, tokens


def pause_cap(mark: str) -> float:
    if any(c in mark for c in "。！？.!?"):
        return SENTENCE_PAUSE
    return COMMA_PAUSE if mark else WORD_PAUSE


def keep_intervals(tokens: tuple[dict, ...], total: float, lang: Lang) -> tuple[Span, ...]:
    """要保留的片段：超过上限的停顿从中间裁短。模型有时在句间留好几秒空白，那是数字静音，裁掉不伤语音。"""
    start: float | None = None
    end = 0.0
    spans: list[Span] = []
    pause_mark = ""
    for tok in tokens:
        if not lang.norm(tok["text"]):
            pause_mark += tok["text"]
            continue
        a, b = tok["start_time"] / 1000, tok["end_time"] / 1000
        if start is None:
            start = max(0.0, a - HEAD_KEEP)
        elif a - end > (cap := pause_cap(pause_mark)):
            spans.append((start, end + cap / 2))
            start = a - cap / 2
        end, pause_mark = b, ""
    if start is None:
        raise VideoError("配音里没有任何字")
    spans.append((start, min(total, end + TAIL_KEEP)))
    return tuple(spans)


def remap(t: float, spans: tuple[Span, ...]) -> float:
    """原始配音里的时刻 → 裁剪后的时刻。"""
    acc = 0.0
    for s, e in spans:
        if t < s:
            return acc
        if t <= e:
            return acc + t - s
        acc += e - s
    return acc


def shot_events(lines: tuple[str, ...], times: tuple[Span, ...], offset: float, lang: Lang) -> tuple[Event, ...]:
    """一个镜头的字幕事件：每块字幕从它第一个字开始，到最后一个字结束。"""
    events: list[Event] = []
    k = 0
    for line in lines:
        for chunk in lang.split_sub(line):
            n = len(lang.norm(chunk))
            if n:
                events.append((offset + times[k][0], offset + times[k + n - 1][1], chunk))
                k += n
    return tuple(events)


def hold_events(events: tuple[Event, ...]) -> tuple[Event, ...]:
    """间隔很短的字幕首尾相接；其余多停留一会儿，最后一条再多停一点。"""
    held = [
        (a, nxt[0] if nxt[0] - b < SUB_HOLD else b + SUB_EXTRA, text)
        for (a, b, text), nxt in zip(events, events[1:])
    ]
    if events:
        a, b, text = events[-1]
        held.append((a, b + LAST_SUB_EXTRA, text))
    return tuple(held)


def clip_frames(shot_starts: tuple[float, ...], total: float) -> tuple[int, ...]:
    """镜头切点（帧号）：切在镜头间停顿的正中，首尾分别是 0 和总帧数。"""
    cuts = [round((s - SHOT_GAP / 2) * FPS) for s in shot_starts[1:]]
    return (0, *cuts, round(total * FPS))
