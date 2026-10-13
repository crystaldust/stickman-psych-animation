"""读取 bili 采集下来的文案：data/<mid>/<bvid>/{meta.json, transcript.json}。"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..collect.bilibili.targets import BVID_PATTERN, MID_PATTERN
from ..collect.bilibili.transcribe import Segment

logger = logging.getLogger(__name__)

BGM_TAG = re.compile(r"发现《(.+?)》")


class CorpusError(LookupError):
    """找不到视频或文案。"""


@dataclass(frozen=True)
class VideoRecord:
    bvid: str
    mid: str
    directory: Path
    meta: dict[str, Any]
    segments: tuple[Segment, ...]

    @property
    def title(self) -> str:
        return str(self.meta.get("title") or self.bvid)

    @property
    def upload_date(self) -> str:
        return str(self.meta.get("upload_date") or "")

    @property
    def tags(self) -> tuple[str, ...]:
        return tuple(t for t in self.meta.get("tags") or [] if not BGM_TAG.fullmatch(t))

    @property
    def bgm(self) -> tuple[str, ...]:
        """B 站自动识别的背景音乐，以「发现《歌名》」形式出现在标签里。"""
        return tuple(m.group(1) for t in self.meta.get("tags") or [] if (m := BGM_TAG.fullmatch(t)))

    @property
    def text(self) -> str:
        return "".join(s.text for s in self.segments)


def _parse(directory: Path) -> tuple[dict[str, Any], tuple[Segment, ...]]:
    meta_path = directory / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    if not isinstance(meta, dict):
        raise TypeError("meta.json 不是对象")
    raw = json.loads((directory / "transcript.json").read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError("transcript.json 不是列表")
    return meta, tuple(Segment(float(s["start"]), float(s["end"]), str(s["text"])) for s in raw)


def _load(directory: Path, mid: str) -> VideoRecord | None:
    """没有文案返回 None；文件损坏抛出 CorpusError。"""
    if not (directory / "transcript.json").exists():
        return None
    try:
        meta, segments = _parse(directory)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CorpusError(f"{directory.name} 的数据损坏：{exc}") from exc
    return VideoRecord(bvid=directory.name, mid=mid, directory=directory, meta=meta, segments=segments)


def _load_or_skip(directory: Path) -> VideoRecord | None:
    try:
        return _load(directory, directory.parent.name)
    except CorpusError as exc:
        logger.warning("跳过：%s", exc)
        return None


def find_video(data_root: Path, bvid: str) -> VideoRecord:
    if not BVID_PATTERN.fullmatch(bvid):
        raise CorpusError(f"不是合法的 BV 号：{bvid}")
    directories = sorted(Path(data_root).glob(f"*/{bvid}"))
    for directory in directories:
        record = _load(directory, directory.parent.name)
        if record is not None:
            return record
    state = "还没有文案" if directories else "找不到"
    raise CorpusError(f"{bvid} {state}，先运行：stickman bili fetch {bvid} --transcribe")


def iter_videos(data_root: Path, mid: str | None = None) -> list[VideoRecord]:
    """已有文案的视频，按发布日期从新到旧；数据损坏的视频会被跳过并记录警告。"""
    if mid is not None and not MID_PATTERN.fullmatch(mid):
        raise CorpusError(f"不是合法的 mid：{mid}")
    pattern = f"{mid}/BV*" if mid else "*/BV*"
    records = (_load_or_skip(d) for d in Path(data_root).glob(pattern) if d.is_dir())
    return sorted((r for r in records if r), key=lambda r: (r.upload_date, r.bvid), reverse=True)


def _date(raw: str) -> str:
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}" if len(raw) == 8 and raw.isdigit() else raw or "未知"


def _stamp(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes:02d}:{secs:02d}"


def render_markdown(record: VideoRecord) -> str:
    meta = record.meta
    duration = _stamp(float(meta.get("duration") or 0))
    lines = [
        f"## {record.title}",
        "",
        f"- BV 号：{record.bvid}（up 主 mid {record.mid}）",
        f"- 发布：{_date(record.upload_date)}　时长：{duration}　播放：{meta.get('view_count', '未知')}",
        f"- 标签：{'、'.join(record.tags) or '无'}",
        f"- 背景音乐：{'、'.join(record.bgm) or '未识别'}",
        "",
        "### 文案",
        "",
        *(f"[{_stamp(s.start)}] {s.text}" for s in record.segments),
        "",
    ]
    return "\n".join(lines)


def render_corpus(records: list[VideoRecord]) -> str:
    header = f"# 文案合集（共 {len(records)} 篇，按发布日期从新到旧）\n\n"
    return header + "\n".join(render_markdown(r) for r in records)
