"""把下载、抽音频、转写串成流程：单个视频处理、up 主列表、批量采集。"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .bili import AccessDeniedError, BiliError, find_output
from .models import VideoEntry
from .storage import Layout, merge_entries, read_index, write_index, write_json
from .transcribe import Segment, write_transcript

logger = logging.getLogger(__name__)

UNKNOWN_MID = "unknown"


class VideoSource(Protocol):
    def list_user_videos(self, mid: str, limit: int | None = None) -> list[VideoEntry]: ...

    def fetch_meta(self, bvid: str) -> dict[str, Any]: ...

    def entry_from_meta(self, info: dict[str, Any]) -> VideoEntry: ...

    def download_audio(self, bvid: str, dest: Path) -> Path: ...

    def download_video(self, bvid: str, dest: Path) -> Path: ...


@dataclass(frozen=True)
class Context:
    client: VideoSource
    layout: Layout
    extract_audio: Callable[[Path, Path], Path]
    transcribe_audio: Callable[[Path], tuple[Segment, ...]]


@dataclass(frozen=True)
class VideoResult:
    bvid: str
    mid: str
    directory: Path
    transcript: Path | None


@dataclass(frozen=True)
class Failure:
    bvid: str
    reason: str


@dataclass(frozen=True)
class CollectReport:
    done: tuple[str, ...]
    skipped: tuple[str, ...]
    failed: tuple[Failure, ...]


def _update_index(ctx: Context, mid: str, entries: list[VideoEntry]) -> None:
    path = ctx.layout.index_path(mid)
    write_index(path, merge_entries(read_index(path), entries))


def _ensure_transcript(ctx: Context, bvid: str, directory: Path) -> Path:
    txt = directory / "transcript.txt"
    if (directory / "transcript.json").exists():
        logger.info("%s: transcript exists, skip", bvid)
        return txt
    source = ctx.client.download_audio(bvid, directory)
    wav = ctx.extract_audio(source, directory / "audio.wav")
    logger.info("%s: transcribing %s", bvid, wav.name)
    write_transcript(directory, ctx.transcribe_audio(wav))
    return txt


def process_video(
    ctx: Context,
    bvid: str,
    *,
    mid: str | None = None,
    video: bool = False,
    audio: bool = False,
    transcribe: bool = False,
) -> VideoResult:
    """取元数据并写入 meta.json 和索引，再按需下载视频、抽音频、转写。

    mid 不传时用元数据里的 uploader_id。transcribe 隐含了抽音频，此时 audio 参数无额外作用。
    """
    meta = ctx.client.fetch_meta(bvid)
    owner = mid or str(meta.get("uploader_id") or UNKNOWN_MID)
    directory = ctx.layout.video_dir(owner, bvid)
    write_json(directory / "meta.json", meta)
    _update_index(ctx, owner, [ctx.client.entry_from_meta(meta)])
    logger.info("%s: %s", bvid, meta.get("title", ""))

    if video:
        ctx.client.download_video(bvid, directory)
    if audio and not transcribe:
        ctx.extract_audio(ctx.client.download_audio(bvid, directory), directory / "audio.wav")
    transcript = _ensure_transcript(ctx, bvid, directory) if transcribe else None
    return VideoResult(bvid=bvid, mid=owner, directory=directory, transcript=transcript)


def _enrich(ctx: Context, mid: str, entry: VideoEntry, known: dict[str, VideoEntry]) -> VideoEntry:
    """扁平列表只有 BV 号：优先用索引里已有的信息，否则请求一次元数据补全。"""
    if entry.title:
        return entry
    cached = known.get(entry.bvid)
    if cached and cached.title:
        return cached
    try:
        meta = ctx.client.fetch_meta(entry.bvid)
    except AccessDeniedError:
        raise
    except BiliError as exc:  # 单条补全失败不影响列表本身，保留只有 BV 号的条目
        logger.warning("%s: metadata unavailable: %s", entry.bvid, exc)
        return entry
    write_json(ctx.layout.video_dir(mid, entry.bvid) / "meta.json", meta)
    return ctx.client.entry_from_meta(meta)


def list_user(ctx: Context, mid: str, limit: int | None = None, *, enrich: bool = True) -> list[VideoEntry]:
    entries = ctx.client.list_user_videos(mid, limit=limit)
    if enrich:
        known = {e.bvid: e for e in read_index(ctx.layout.index_path(mid))}
        entries = [_enrich(ctx, mid, e, known) for e in entries]
    _update_index(ctx, mid, entries)
    return entries


def _is_complete(directory: Path, video: bool, audio: bool, transcribe: bool) -> bool:
    return (
        (directory / "meta.json").exists()
        and (not transcribe or (directory / "transcript.json").exists())
        and (not audio or (directory / "audio.wav").exists())
        and (not video or find_output(directory, "video") is not None)
    )


def collect_user(
    ctx: Context,
    mid: str,
    *,
    limit: int | None = None,
    video: bool = False,
    audio: bool = False,
    transcribe: bool = False,
) -> CollectReport:
    """批量处理 up 主的视频。单条失败记入报告继续；遇到风控立即中止，避免加重封禁。"""
    done: list[str] = []
    skipped: list[str] = []
    failed: list[Failure] = []

    entries = list_user(ctx, mid, limit=limit, enrich=False)  # process_video 会取完整元数据
    for index, entry in enumerate(entries, start=1):
        if _is_complete(ctx.layout.video_dir(mid, entry.bvid), video, audio, transcribe):
            skipped.append(entry.bvid)
            continue
        logger.info("[%d/%d] %s", index, len(entries), entry.bvid)
        try:
            process_video(ctx, entry.bvid, mid=mid, video=video, audio=audio, transcribe=transcribe)
            done.append(entry.bvid)
        except AccessDeniedError:
            raise
        except Exception as exc:  # 单个视频的任何失败都不中断批量任务，带堆栈记日志并写入报告
            logger.warning("%s failed", entry.bvid, exc_info=True)
            failed.append(Failure(entry.bvid, f"{type(exc).__name__}: {exc}"))

    return CollectReport(done=tuple(done), skipped=tuple(skipped), failed=tuple(failed))
