"""把下载、抽音频、转写串成流程：单个视频处理、up 主列表、批量采集。"""

import json
import logging
import threading
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .bili import AccessDeniedError, BiliError, find_output
from .models import VideoEntry
from .storage import Layout, merge_entries, read_index, write_index, write_json, write_text
from .transcribe import Segment, format_srt, write_transcript

logger = logging.getLogger(__name__)

UNKNOWN_MID = "unknown"
MAX_JOBS = 4

# videos.jsonl 是读-改-写，多个网络 worker 并发更新时必须串行化（只保护同一进程内）
_INDEX_LOCK = threading.Lock()


class _Stopped(Exception):
    """其他 worker 已触发风控，当前视频放弃后续网络请求。"""


class VideoSource(Protocol):
    def list_user_videos(self, mid: str, limit: int | None = None) -> list[VideoEntry]: ...

    def fetch_meta(self, bvid: str) -> dict[str, Any]: ...

    def fetch_subtitles(self, bvid: str) -> tuple[str, tuple[Segment, ...]] | None: ...

    def entry_from_meta(self, info: dict[str, Any]) -> VideoEntry: ...

    def download_audio(self, bvid: str, dest: Path) -> Path: ...

    def download_video(self, bvid: str, dest: Path) -> Path: ...


@dataclass(frozen=True)
class Context:
    client: VideoSource
    layout: Layout
    extract_audio: Callable[[Path, Path], Path]
    transcribe_audio: Callable[[Path], tuple[Segment, ...]]
    use_subtitles: bool = True
    transcriber_name: str = "whisper"


@dataclass(frozen=True)
class VideoResult:
    bvid: str
    mid: str
    directory: Path
    transcript: Path | None
    transcript_source: str | None = None
    transcript_reused: bool = False
    subtitles: Path | None = None


@dataclass(frozen=True)
class Failure:
    bvid: str
    reason: str


@dataclass(frozen=True)
class CollectReport:
    done: tuple[str, ...]
    skipped: tuple[str, ...]
    failed: tuple[Failure, ...]


@dataclass(frozen=True)
class _Prepared:
    """网络阶段的产物；pending_wav 不为空表示还需要 Whisper 转写。"""

    result: VideoResult
    pending_wav: Path | None = None


def _update_index(ctx: Context, mid: str, entries: list[VideoEntry]) -> None:
    path = ctx.layout.index_path(mid)
    with _INDEX_LOCK:
        write_index(path, merge_entries(read_index(path), entries))


def _existing_source(directory: Path) -> str | None:
    try:
        data = json.loads((directory / "transcript.info.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get("source") if isinstance(data, dict) else None


def _official_subtitles(ctx: Context, bvid: str) -> tuple[str, tuple[Segment, ...]] | None:
    try:
        return ctx.client.fetch_subtitles(bvid)
    except AccessDeniedError:
        raise
    except BiliError as exc:  # 字幕接口失败不影响转写，退回 Whisper
        logger.warning("%s: subtitles unavailable, falling back to whisper: %s", bvid, exc)
        return None


def _save_official_subtitles(ctx: Context, bvid: str, directory: Path) -> Path | None:
    """保存 B 站官方字幕作为对照（需登录）。它没有标点、按显示长度断句，所以不作为主文案。"""
    found = _official_subtitles(ctx, bvid) if ctx.use_subtitles else None
    if not found:
        return None
    lang, segments = found
    path = directory / f"subtitles.{lang}.srt"  # lang 来自 SUBTITLE_PREFERENCE 白名单
    write_text(path, format_srt(segments))
    logger.info("%s: saved official subtitles (%s)", bvid, lang)
    return path


def _prepare_transcript(
    ctx: Context, bvid: str, directory: Path, wav: Path | None
) -> tuple[bool, Path | None, Path | None]:
    """网络阶段的转写准备：保存官方字幕对照，并准备好 wav 等待 Whisper。

    wav 是 --audio 已经抽好的音频，会直接复用。已有文案时整步跳过。
    返回 (是否复用已有文案, 官方字幕路径, 待转写的 wav)。
    """
    if (directory / "transcript.json").exists():
        logger.info("%s: transcript exists, skip", bvid)
        return True, None, None
    subtitles = _save_official_subtitles(ctx, bvid, directory)
    return False, subtitles, wav or _extract_wav(ctx, bvid, directory)


def _extract_wav(ctx: Context, bvid: str, directory: Path) -> Path:
    return ctx.extract_audio(ctx.client.download_audio(bvid, directory), directory / "audio.wav")


def _never_stop() -> bool:
    return False


def _prepare(
    ctx: Context,
    bvid: str,
    *,
    mid: str | None,
    video: bool,
    audio: bool,
    transcribe: bool,
    should_stop: Callable[[], bool] = _never_stop,
) -> _Prepared:
    """所有需要访问 B 站的步骤：元数据、视频、音频、字幕。每一步前检查是否已被风控叫停。"""

    def checkpoint() -> None:
        if should_stop():
            raise _Stopped(bvid)

    checkpoint()
    meta = ctx.client.fetch_meta(bvid)
    owner = mid or str(meta.get("uploader_id") or UNKNOWN_MID)
    directory = ctx.layout.video_dir(owner, bvid)
    write_json(directory / "meta.json", meta)
    _update_index(ctx, owner, [ctx.client.entry_from_meta(meta)])
    logger.info("%s: %s", bvid, meta.get("title", ""))

    if video:
        checkpoint()
        ctx.client.download_video(bvid, directory)
    wav = None
    if audio:
        checkpoint()
        wav = _extract_wav(ctx, bvid, directory)

    reused, subtitles, pending = False, None, None
    if transcribe:
        checkpoint()
        reused, subtitles, pending = _prepare_transcript(ctx, bvid, directory, wav)
    result = VideoResult(
        bvid=bvid,
        mid=owner,
        directory=directory,
        transcript=directory / "transcript.txt" if transcribe else None,
        transcript_source=_existing_source(directory) if reused else None,
        transcript_reused=reused,
        subtitles=subtitles,
    )
    return _Prepared(result=result, pending_wav=pending)


def _finish(ctx: Context, prepared: _Prepared) -> VideoResult:
    """本地 GPU 阶段：需要时用 Whisper 转写。只应在单个线程里调用。"""
    if prepared.pending_wav is None:
        return prepared.result
    result = prepared.result
    logger.info("%s: transcribing %s", result.bvid, prepared.pending_wav.name)
    write_transcript(result.directory, ctx.transcribe_audio(prepared.pending_wav), source=ctx.transcriber_name)
    return replace(result, transcript_source=ctx.transcriber_name)


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

    mid 不传时用元数据里的 uploader_id。转写用 Whisper；登录时另存 B 站官方字幕作为对照。
    """
    prepared = _prepare(ctx, bvid, mid=mid, video=video, audio=audio, transcribe=transcribe)
    return _finish(ctx, prepared)


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


def _run_pipeline(
    ctx: Context, mid: str, bvids: list[str], *, jobs: int, video: bool, audio: bool, transcribe: bool
) -> tuple[set[str], dict[str, Failure]]:
    """网络阶段在线程池里并发（最多 jobs 个），Whisper 在调用线程里逐个执行，两者流水线重叠。

    - 提前准备的视频最多 jobs+1 个，避免 Whisper 跟不上时把后面所有音频都堆到磁盘上
    - 任何 worker 遇到风控立即自己发出停止信号，不等主线程处理完手头的转写
    """
    done: set[str] = set()
    failed: dict[str, Failure] = {}
    stop = threading.Event()
    window = jobs + 1
    queue = iter(bvids)
    in_flight: dict[Future[_Prepared | None], str] = {}

    def network_stage(bvid: str) -> _Prepared | None:
        try:
            return _prepare(
                ctx, bvid, mid=mid, video=video, audio=audio, transcribe=transcribe, should_stop=stop.is_set
            )
        except _Stopped:
            return None
        except AccessDeniedError:
            stop.set()
            raise

    def refill(pool: ThreadPoolExecutor) -> None:
        while len(in_flight) < window and not stop.is_set():
            bvid = next(queue, None)
            if bvid is None:
                return
            in_flight[pool.submit(network_stage, bvid)] = bvid

    pool = ThreadPoolExecutor(max_workers=jobs, thread_name_prefix="bili-net")
    try:
        refill(pool)
        while in_flight:
            completed, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in completed:
                bvid = in_flight.pop(future)
                _consume(ctx, bvid, future, done, failed)
            refill(pool)
    except BaseException:
        stop.set()
        running = sum(1 for f in in_flight if f.running())
        if running:
            logger.warning("停止中：等待 %d 个正在进行的下载结束", running)
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    return done, failed


def _consume(
    ctx: Context, bvid: str, future: Future, done: set[str], failed: dict[str, Failure]
) -> None:
    """在调用线程里收尾一个视频（必要时跑 Whisper）。风控向上抛出，其他失败记入 failed。"""
    try:
        prepared = future.result()
        if prepared is None:
            return
        _finish(ctx, prepared)
        done.add(bvid)
        logger.info("%s done (%d finished)", bvid, len(done))
    except AccessDeniedError:
        raise
    except Exception as exc:  # 单个视频的任何失败都不中断批量任务，带堆栈记日志并写入报告
        logger.warning("%s failed", bvid, exc_info=True)
        failed[bvid] = Failure(bvid, f"{type(exc).__name__}: {exc}")


def collect_user(
    ctx: Context,
    mid: str,
    *,
    limit: int | None = None,
    video: bool = False,
    audio: bool = False,
    transcribe: bool = False,
    jobs: int = 1,
) -> CollectReport:
    """批量处理 up 主的视频。单条失败记入报告继续；遇到风控立即中止，避免加重封禁。

    jobs 是访问 B 站的并发数（1–4）；Whisper 始终一次只跑一个（实测 M 系列 GPU 并发反而更慢）。
    """
    if not 1 <= jobs <= MAX_JOBS:
        raise ValueError(f"jobs 必须在 1 到 {MAX_JOBS} 之间")

    entries = list_user(ctx, mid, limit=limit, enrich=False)  # _prepare 会取完整元数据
    order = list(dict.fromkeys(e.bvid for e in entries))  # 多 P 视频会让同一个 BV 号出现多次
    skipped = [b for b in order if _is_complete(ctx.layout.video_dir(mid, b), video, audio, transcribe)]
    todo = [b for b in order if b not in skipped]

    done, failed = _run_pipeline(ctx, mid, todo, jobs=jobs, video=video, audio=audio, transcribe=transcribe)
    return CollectReport(
        done=tuple(b for b in order if b in done),
        skipped=tuple(skipped),
        failed=tuple(failed[b] for b in order if b in failed),
    )
