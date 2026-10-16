"""把每个镜头的配音裁掉过长停顿，拼成一条 voice.wav，同时算出镜头起点和字幕时间。"""

import json
from dataclasses import dataclass
from pathlib import Path

from .config import LEAD, SHOT_GAP, TAIL
from .errors import VideoError
from .ffmpeg import Runner, ffmpeg, probe_duration
from .lang import LANGS
from .timeline import Event, Span, char_times, hold_events, keep_intervals, remap, shot_events
from .workdir import WorkDir

Segment = tuple[str, tuple[str, ...]]  # (镜头 id, 这个镜头的旁白行)


@dataclass(frozen=True)
class VoiceTimeline:
    shot_starts: tuple[float, ...]
    total: float
    events: tuple[Event, ...]


def _silence(seconds: float) -> list[str]:
    return ["-f", "lavfi", "-t", f"{seconds:.3f}", "-i", "anullsrc=r=44100:cl=mono"]


def missing_voice(work: WorkDir, ids: tuple[str, ...]) -> tuple[str, ...]:
    """缺音频或缺元数据的镜头（两者都在才算配好音）。"""
    return tuple(sid for sid in ids if not (work.voice(sid).is_file() and work.voice_meta(sid).is_file()))


def read_meta(work: WorkDir, sid: str) -> dict:
    path = work.voice_meta(sid)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VideoError(f"{sid}：缺少配音元数据 {path}") from exc
    except json.JSONDecodeError as exc:
        raise VideoError(f"{sid}：配音元数据不是合法的 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise VideoError(f"{sid}：配音元数据应该是 JSON 对象")
    return data


def _trim(src: Path, spans: tuple[Span, ...], out: Path, runner: Runner) -> None:
    parts = [f"[0:a]atrim={s:.3f}:{e:.3f},asetpts=PTS-STARTPTS[p{i}]" for i, (s, e) in enumerate(spans)]
    joined = "".join(f"[p{i}]" for i in range(len(spans)))
    graph = ";".join(parts) + f";{joined}concat=n={len(spans)}:v=0:a=1[out]"
    ffmpeg(["-i", src, "-filter_complex", graph, "-map", "[out]", out], runner)


def _concat(inputs: list[str], out: Path, runner: Runner) -> None:
    n = inputs.count("-i")
    labels = [f"[{i}:a]aresample=44100,aformat=channel_layouts=mono[a{i}]" for i in range(n)]
    joined = "".join(f"[a{i}]" for i in range(n))
    graph = ";".join(labels) + f";{joined}concat=n={n}:v=0:a=1[out]"
    ffmpeg([*inputs, "-filter_complex", graph, "-map", "[out]", out], runner)


def build_voice(work: WorkDir, segments: tuple[Segment, ...], runner: Runner) -> VoiceTimeline:
    lang = LANGS[work.lang]
    missing = missing_voice(work, tuple(sid for sid, _ in segments))
    if missing:
        raise VideoError(f"这些镜头还没有配音：{', '.join(missing)}")
    work.trim_dir.mkdir(parents=True, exist_ok=True)
    inputs = _silence(LEAD)
    t = LEAD
    starts: list[float] = []
    events: list[Event] = []
    for i, (sid, lines) in enumerate(segments):
        if i:
            inputs += _silence(SHOT_GAP)
            t += SHOT_GAP
        starts.append(t)
        mp3, seg = work.voice(sid), work.trimmed(sid)
        times, tokens = char_times(read_meta(work, sid), lang.norm(lang.joiner.join(lines)),
                                   f"{work.voice_dir.name}/{sid}.mp3", lang)
        spans = keep_intervals(tokens, probe_duration(mp3, runner), lang)
        _trim(mp3, spans, seg, runner)
        events += shot_events(lines, tuple((remap(a, spans), remap(b, spans)) for a, b in times), t, lang)
        inputs += ["-i", str(seg)]
        t += probe_duration(seg, runner)
    inputs += _silence(TAIL)
    _concat(inputs, work.voice_wav, runner)
    return VoiceTimeline(tuple(starts), probe_duration(work.voice_wav, runner), hold_events(tuple(events)))
