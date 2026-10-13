"""B 站官方字幕：从 yt-dlp 的 subtitles 字段里挑出中文字幕，并解析成 Segment。"""

import re
from typing import Any

from .transcribe import Segment

# 人工字幕优先，其次是 B 站 AI 字幕
SUBTITLE_PREFERENCE = ("zh-CN", "zh-Hans", "zh", "ai-zh")
# 小时位可省略；位数设上限，避免恶意数据让 int() 溢出
_STAMP = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[,.](\d{3})"
TIME_LINE = re.compile(rf"{_STAMP}\s*-->\s*{_STAMP}")
_LATIN_END = re.compile(r"[A-Za-z0-9]$")
_LATIN_START = re.compile(r"^[A-Za-z0-9]")


def _seconds(h: str | None, m: str, s: str, ms: str) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _join(lines: list[str]) -> str:
    """中文多行直接拼接；英文行之间补一个空格。"""
    text = ""
    for line in lines:
        gap = " " if text and _LATIN_END.search(text) and _LATIN_START.search(line) else ""
        text += gap + line
    return text


def _parse_block(block: str) -> Segment | None:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        match = TIME_LINE.fullmatch(line)
        if match:
            g = match.groups()
            body = _join(lines[index + 1:])
            return Segment(_seconds(*g[:4]), _seconds(*g[4:]), body) if body else None
    return None


def parse_srt(text: str) -> tuple[Segment, ...]:
    blocks = re.split(r"\r?\n\s*\r?\n", text.lstrip("\ufeff").strip())
    return tuple(seg for seg in map(_parse_block, blocks) if seg is not None)


def pick_subtitle(subtitles: dict[str, Any] | None) -> tuple[str, str] | None:
    """返回 (语言代码, SRT 文本)；没有可用的中文字幕时返回 None。"""
    for lang in SUBTITLE_PREFERENCE:
        for entry in (subtitles or {}).get(lang) or []:
            if entry.get("ext") == "srt" and entry.get("data"):
                return lang, entry["data"]
    return None
