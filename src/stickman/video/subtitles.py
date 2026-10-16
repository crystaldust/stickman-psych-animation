"""字幕文件：烧进画面的 ASS，以及给 YouTube 上传字幕轨用的 SRT。"""

Event = tuple[float, float, str]  # 和 timeline.Event 相同；不从 timeline 导入，免得 lang → subtitles → timeline 循环

SUB_STYLE = "Style: Default,PingFang SC,66,&H00FFFFFF,&H00000000,&H00000000,1,1,5,0,2,70\n"
SUB_STYLE_EN = "Style: Default,Helvetica Neue,60,&H00FFFFFF,&H00000000,&H00000000,1,1,5,0,2,70\n"
_HEAD = (
    "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n"
    "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
    "Bold, BorderStyle, Outline, Shadow, Alignment, MarginV\n"
)
_EVENTS = "\n[Events]\nFormat: Layer, Start, End, Style, Text\n"


def ts(sec: float) -> str:
    """ASS 时间（厘秒）。先取整再拆分，否则 59.996 会变成 0:00:60.00。"""
    cs = round(sec * 100)
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6_000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def ass_document(events: tuple[Event, ...], style: str = SUB_STYLE) -> str:
    body = "\n".join(f"Dialogue: 0,{ts(a)},{ts(b)},Default,{text}" for a, b, text in events)
    return _HEAD + style + _EVENTS + body + "\n"


def srt_ts(sec: float) -> str:
    """SRT 时间（毫秒，逗号分隔），同样先取整再拆分。"""
    ms = round(sec * 1000)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_document(events: tuple[Event, ...]) -> str:
    return "\n".join(f"{i}\n{srt_ts(a)} --> {srt_ts(b)}\n{text}\n" for i, (a, b, text) in enumerate(events, 1))
