"""从草稿生成工作目录：旁白逐行写入 narration.txt，每条「> 画面」生成一个待填写的镜头。

草稿里画面提示写在它所配的旁白之后，所以一条提示结束一个镜头，镜头从上一条提示后的第一行旁白开始。
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .config import STYLE_PROMPT
from .errors import VideoError
from .workdir import WorkDir

_FENCE = "---"
_CUE_PREFIXES = ("画面：", "画面:")


@dataclass(frozen=True)
class Block:
    line: int  # 这个镜头第一行旁白的行号（从 1 开始）
    cue: str


@dataclass(frozen=True)
class Draft:
    lines: tuple[str, ...]
    blocks: tuple[Block, ...]
    warnings: tuple[str, ...]


def _body(text: str) -> list[str]:
    rows = text.splitlines()
    if not rows or rows[0].strip() != _FENCE:
        return rows
    for i, row in enumerate(rows[1:], start=1):
        if row.strip() == _FENCE:
            return rows[i + 1:]
    raise VideoError("草稿的 front matter 没有结束的 ---")


def _cue_text(row: str) -> str:
    cue = row.lstrip(">").strip()
    for prefix in _CUE_PREFIXES:
        cue = cue.removeprefix(prefix)
    return cue.strip()


def parse_draft(text: str) -> Draft:
    lines: list[str] = []
    blocks: list[Block] = []
    block_start = 1
    for row in (r.strip() for r in _body(text)):
        if not row:
            continue
        if row.startswith(">"):
            if block_start > len(lines):
                raise VideoError(f"画面提示「{_cue_text(row)}」前面没有旁白")
            blocks.append(Block(block_start, _cue_text(row)))
            block_start = len(lines) + 1
        else:
            lines.append(row)
    if not lines:
        raise VideoError("草稿里没有旁白")
    warnings: tuple[str, ...] = ()
    if block_start <= len(lines):
        blocks.append(Block(block_start, ""))
        warnings = (f"第 {block_start} 行之后的旁白没有画面提示，已单独作为最后一个镜头",)
    return Draft(tuple(lines), tuple(blocks), warnings)


def skeleton(draft: Draft) -> dict:
    shots = [
        {"id": f"s{i:02d}", "line": b.line, "cue": b.cue, "bg": "", "scene": ""}
        for i, b in enumerate(draft.blocks, start=1)
    ]
    return {"style": STYLE_PROMPT, "shots": shots}


def write_workdir(draft: Draft, work: WorkDir, force: bool) -> None:
    existing = [p for p in (work.narration, work.shots) if p.exists()]
    if existing and not force:
        raise VideoError(f"{existing[0]} 已存在；确认要覆盖请加 --force")
    work.root.mkdir(parents=True, exist_ok=True)
    work.narration.write_text("\n".join(draft.lines) + "\n", encoding="utf-8")
    work.shots.write_text(json.dumps(skeleton(draft), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def read_draft(path: Path) -> Draft:
    if not path.is_file():
        raise VideoError(f"找不到草稿：{path}")
    return parse_draft(path.read_text(encoding="utf-8"))
