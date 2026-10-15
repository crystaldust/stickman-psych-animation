"""分镜：读取并校验 narration.txt + shots.json。

一个镜头从第 line 行旁白开始，到下一个镜头的前一行为止。校验不过就不往下走，
这样没填完的骨架永远不会被拿去调付费接口。镜头可以带 motion（见 motion.py）改为分层，
但分层镜头最多占三分之一。
"""

import json
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import VideoError
from .motion import Motion, layer_prompt, max_layered, parse_motion
from .workdir import WorkDir, safe_id


@dataclass(frozen=True)
class Shot:
    id: str
    line: int
    bg: str
    scene: str
    motion: Motion | None = None


@dataclass(frozen=True)
class ImageTarget:
    """要出的一张图：整幅镜头图（id 是 s01）或分层镜头的一层（id 是 s03:fg）。"""
    id: str
    shot_id: str
    prompt: str
    path: Path

    @property
    def rejected_stem(self) -> str:
        return self.id.replace(":", "-")


@dataclass(frozen=True)
class ShotSpec:
    style: str
    shots: tuple[Shot, ...]

    def prompt(self, shot: Shot) -> str:
        return self.style.format(bg=shot.bg) + shot.scene

    def ids(self) -> tuple[str, ...]:
        return tuple(s.id for s in self.shots)

    def targets(self, work: WorkDir) -> tuple[ImageTarget, ...]:
        """整条视频要出的图。分层镜头只出各层；glow 没有图层，用整幅图。"""
        out: list[ImageTarget] = []
        for s in self.shots:
            if s.motion is None or not s.motion.layers:
                out.append(ImageTarget(s.id, s.id, self.prompt(s), work.image(s.id)))
                continue
            out += [ImageTarget(f"{s.id}:{layer.name}", s.id, layer_prompt(self.style, s.bg, s.motion, layer),
                                work.layer(s.id, layer.name)) for layer in s.motion.layers]
        return tuple(out)


def _style_errors(style: Any) -> list[str]:
    if not isinstance(style, str):
        return ["style 应该是字符串"]
    try:
        names = [name for _, name, _, _ in string.Formatter().parse(style) if name is not None]
    except ValueError as exc:
        return [f"style 里的花括号不成对：{exc}"]
    if "bg" not in names or set(names) != {"bg"}:
        return ["style 里只能有 {bg} 一个占位符，而且必须有"]
    return []


def _shot_errors(raw: Any, index: int, n_lines: int, prev_line: int, seen: set[str]) -> list[str]:
    if not isinstance(raw, dict):
        return [f"第 {index + 1} 个镜头应该是 JSON 对象"]
    sid = raw.get("id")
    label = sid if isinstance(sid, str) else f"第 {index + 1} 个镜头"
    errors = []
    try:
        safe_id(sid if isinstance(sid, str) else "")
    except VideoError as exc:
        errors.append(str(exc))
    if isinstance(sid, str) and sid in seen:
        errors.append(f"{label}：id 重复")
    line = raw.get("line")
    if type(line) is not int:
        errors.append(f"{label}：line 应该是整数")
    elif index == 0 and line != 1:
        errors.append(f"{label}：第一个镜头必须从第 1 行开始，否则前面的旁白会丢")
    elif line <= prev_line:
        errors.append(f"{label}：line 必须严格递增")
    elif line > n_lines:
        errors.append(f"{label}：line={line} 超出旁白行数 {n_lines}")
    for field in ("bg", "scene"):
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{label}：{field} 不能为空")
    return errors + parse_motion(raw.get("motion"), label)[1]


def _cap_errors(raw_shots: list) -> list[str]:
    layered = [r.get("id") for r in raw_shots if isinstance(r, dict) and r.get("motion") is not None]
    cap = max_layered(len(raw_shots))
    if len(layered) <= cap:
        return []
    return [f"分层镜头有 {len(layered)} 个（{', '.join(map(str, layered))}），上限 {cap} 个（镜头数的三分之一）："
            "只给环境要自己动的镜头分层，其余去掉 motion"]


def _read_json(work: WorkDir) -> dict:
    try:
        data = json.loads(work.shots.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VideoError(f"找不到 {work.shots}") from exc
    except json.JSONDecodeError as exc:
        raise VideoError(f"{work.shots} 不是合法的 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise VideoError(f"{work.shots} 应该是一个 JSON 对象")
    return data


def read_lines(work: WorkDir) -> tuple[str, ...]:
    try:
        text = work.narration.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise VideoError(f"找不到 {work.narration}") from exc
    return tuple(line.strip() for line in text.splitlines() if line.strip())


def load_spec(work: WorkDir) -> tuple[ShotSpec, tuple[str, ...]]:
    lines = read_lines(work)
    data = _read_json(work)
    raw_shots = data.get("shots")
    errors = _style_errors(data.get("style"))
    if not isinstance(raw_shots, list) or not raw_shots:
        errors.append("shots.json 里没有镜头")
        raw_shots = []
    seen: set[str] = set()
    prev_line = 0
    for index, raw in enumerate(raw_shots):
        errors += _shot_errors(raw, index, len(lines), prev_line, seen)
        if isinstance(raw, dict):
            if isinstance(raw.get("id"), str):
                seen.add(raw["id"])
            prev_line = raw["line"] if type(raw.get("line")) is int else prev_line
    errors += _cap_errors(raw_shots)
    if errors:
        raise VideoError(f"{work.shots} 有 {len(errors)} 处问题：\n  " + "\n  ".join(errors))
    shots = tuple(Shot(r["id"], r["line"], r["bg"].strip(), r["scene"].strip(), parse_motion(r.get("motion"), r["id"])[0])
                  for r in raw_shots)
    return ShotSpec(data["style"], shots), lines


def shot_lines(spec: ShotSpec, lines: tuple[str, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """每个镜头对应的旁白行。"""
    starts = [s.line for s in spec.shots] + [len(lines) + 1]
    return tuple((s.id, lines[a - 1:b - 1]) for s, a, b in zip(spec.shots, starts, starts[1:]))
