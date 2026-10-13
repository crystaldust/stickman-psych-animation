"""采集数据的目录布局，以及原子写入的 JSON / JSONL 读写。"""

import json
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import fields, replace
from pathlib import Path
from typing import Any

from .models import VideoEntry

SAFE_SEGMENT = re.compile(r"[A-Za-z0-9_-]+")
FILE_MODE = 0o644


def _safe(segment: str) -> str:
    if not SAFE_SEGMENT.fullmatch(segment):
        raise ValueError(f"unsafe path segment: {segment!r}")
    return segment


class Layout:
    """data/<mid>/videos.jsonl 与 data/<mid>/<bvid>/ 的路径规则。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def user_dir(self, mid: str) -> Path:
        return self.root / _safe(mid)

    def index_path(self, mid: str) -> Path:
        return self.user_dir(mid) / "videos.jsonl"

    def video_dir(self, mid: str, bvid: str) -> Path:
        return self.user_dir(mid) / _safe(bvid)


def write_text(path: Path, content: str) -> None:
    """先写临时文件再 rename，避免中断时留下半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp, FILE_MODE)  # mkstemp 默认 0600
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def read_index(path: Path) -> list[VideoEntry]:
    if not path.exists():
        return []
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entries.append(VideoEntry.from_dict(json.loads(line)))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ValueError(f"{path}: line {number} is not a valid entry ({exc})") from exc
    return entries


def write_index(path: Path, entries: Iterable[VideoEntry]) -> None:
    lines = (json.dumps(e.to_dict(), ensure_ascii=False) for e in entries)
    write_text(path, "".join(f"{line}\n" for line in lines))


def _combine(old: VideoEntry, new: VideoEntry) -> VideoEntry:
    updates = {
        f.name: getattr(new, f.name)
        for f in fields(VideoEntry)
        if getattr(new, f.name) not in (None, "")
    }
    return replace(old, **updates)


def merge_entries(old: Iterable[VideoEntry], new: Iterable[VideoEntry]) -> list[VideoEntry]:
    """按 bvid 去重合并；新数据里的空字段不覆盖旧值，顺序保持首次出现的位置。"""
    merged: dict[str, VideoEntry] = {e.bvid: e for e in old}
    for entry in new:
        current = merged.get(entry.bvid)
        merged[entry.bvid] = _combine(current, entry) if current else entry
    return list(merged.values())
