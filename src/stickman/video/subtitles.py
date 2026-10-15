"""ASS 字幕文件。"""

from .timeline import Event

SUB_STYLE = "Style: Default,PingFang SC,66,&H00FFFFFF,&H00000000,&H00000000,1,1,5,0,2,70\n"
_HEADER = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
    "Bold, BorderStyle, Outline, Shadow, Alignment, MarginV\n" + SUB_STYLE + "\n"
    "[Events]\nFormat: Layer, Start, End, Style, Text\n"
)


def ts(sec: float) -> str:
    """ASS 时间（厘秒）。先取整再拆分，否则 59.996 会变成 0:00:60.00。"""
    cs = round(sec * 100)
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6_000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def ass_document(events: tuple[Event, ...]) -> str:
    body = "\n".join(f"Dialogue: 0,{ts(a)},{ts(b)},Default,{text}" for a, b, text in events)
    return _HEADER + body + "\n"
