"""对 yt-dlp 的薄封装：列 up 主视频、取元数据、下载音视频。"""

import logging
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yt_dlp
from yt_dlp.utils import YoutubeDLError

from .models import VideoEntry
from .subtitles import parse_srt, pick_subtitle
from .targets import BVID_PATTERN, Target
from .transcribe import Segment

logger = logging.getLogger(__name__)

RISK_CONTROL_CODES = ("(352)", "(412)", "-352", "-412")
META_FIELDS = (
    "id", "title", "description", "duration", "view_count", "like_count",
    "comment_count", "upload_date", "timestamp", "tags", "uploader", "uploader_id",
    "thumbnail", "webpage_url",
)
URL_FIELDS = ("thumbnail", "webpage_url")

YDLFactory = Callable[[dict[str, Any]], Any]


class BiliError(RuntimeError):
    """B 站请求或下载失败。"""


class AccessDeniedError(BiliError):
    """被 B 站风控拦截（-352 / 412），通常需要登录 cookies。"""


@dataclass(frozen=True)
class ClientOptions:
    cookies_from_browser: str | None = None
    cookie_file: Path | None = None
    sleep_seconds: float = 1.0
    retries: int = 3

    def __post_init__(self) -> None:
        if self.cookies_from_browser and self.cookie_file:
            raise ValueError("cookies_from_browser 与 cookie_file 只能二选一")


class BiliClient:
    def __init__(self, options: ClientOptions, ydl_factory: YDLFactory = yt_dlp.YoutubeDL) -> None:
        self.options = options
        self._factory = ydl_factory

    def list_user_videos(self, mid: str, limit: int | None = None) -> list[VideoEntry]:
        extra: dict[str, Any] = {"extract_flat": "in_playlist"}
        if limit is not None:
            extra["playlistend"] = limit
        info = self._extract(Target("user", mid).url, extra)
        entries = []
        for raw in info.get("entries") or []:
            bvid = _normalise_bvid(raw.get("id") if raw else None)
            if bvid is None:
                logger.warning("skip listing entry with unexpected id: %r", raw and raw.get("id"))
                continue
            entries.append(_entry(bvid, raw))
        return entries

    def fetch_meta(self, bvid: str) -> dict[str, Any]:
        # 只要元数据；B 站播放地址接口偶发风控时不应让元数据一起失败
        extra = {"noplaylist": True, "ignore_no_formats_error": True}
        info = self._extract(Target("video", bvid).url, extra)
        if _normalise_bvid(info.get("id")) != bvid:
            raise BiliError(f"video id mismatch: requested {bvid}, got {info.get('id')!r}")
        meta = {key: info[key] for key in META_FIELDS if key in info}
        return {**meta, "id": bvid, **{k: _strip_query(meta[k]) for k in URL_FIELDS if meta.get(k)}}

    def fetch_subtitles(self, bvid: str) -> tuple[str, tuple[Segment, ...]] | None:
        """B 站官方字幕（需登录 cookies）。返回 (语言代码, 分段)，没有可用字幕时返回 None。"""
        extra = {"noplaylist": True, "ignore_no_formats_error": True, "writesubtitles": True}
        info = self._extract(Target("video", bvid).url, extra)
        picked = pick_subtitle(info.get("subtitles"))
        if not picked:
            return None
        lang, srt = picked
        segments = parse_srt(srt)
        return (lang, segments) if segments else None

    @staticmethod
    def entry_from_meta(info: dict[str, Any]) -> VideoEntry:
        return _entry(info["id"], info)

    def download_audio(self, bvid: str, dest: Path) -> Path:
        return self._download(bvid, dest, "source", {"format": "bestaudio/best"})

    def download_video(self, bvid: str, dest: Path) -> Path:
        extra = {"format": "bestvideo+bestaudio/best", "merge_output_format": "mp4"}
        return self._download(bvid, dest, "video", extra)

    def _download(self, bvid: str, dest: Path, stem: str, extra: dict[str, Any]) -> Path:
        existing = find_output(dest, stem)
        if existing:
            logger.info("skip download, exists: %s", existing)
            return existing
        dest.mkdir(parents=True, exist_ok=True)
        # outtmpl 会解析 %(...)s，目录名里的 % 需要转义
        template = str(dest).replace("%", "%%") + f"/{stem}.%(ext)s"
        params = {**extra, "outtmpl": template, "noplaylist": True}
        self._extract(Target("video", bvid).url, params, download=True)
        path = find_output(dest, stem)
        if not path:
            raise BiliError(f"yt-dlp reported success but no file was written for {bvid}")
        return path

    def _base_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "retries": self.options.retries,
            "sleep_interval_requests": self.options.sleep_seconds,
        }
        if self.options.cookies_from_browser:
            params["cookiesfrombrowser"] = (self.options.cookies_from_browser,)
        return params

    @contextmanager
    def _cookie_file_copy(self) -> Iterator[dict[str, Any]]:
        """yt-dlp 退出时会把 cookie jar 回写到 cookiefile，所以只给它一个用完即删的 0600 副本。"""
        source = self.options.cookie_file
        if not source:
            yield {}
            return
        if not source.is_file():
            raise BiliError(f"cookies 文件不存在：{source}")
        fd, tmp = tempfile.mkstemp(prefix="bili-cookies-", suffix=".txt")
        os.close(fd)
        try:
            shutil.copyfile(source, tmp)
            yield {"cookiefile": tmp}
        finally:
            Path(tmp).unlink(missing_ok=True)

    def _extract(self, url: str, extra: dict[str, Any], download: bool = False) -> dict[str, Any]:
        with self._cookie_file_copy() as cookie_params:
            params = {**self._base_params(), **cookie_params, **extra}
            try:
                with self._factory(params) as ydl:
                    info = ydl.extract_info(url, download=download)
            except YoutubeDLError as exc:
                message = str(exc)
                if any(code in message for code in RISK_CONTROL_CODES):
                    raise AccessDeniedError("被 B 站风控拦截，请用登录过 B 站的浏览器 cookies 重试") from exc
                raise BiliError(message) from exc
        if not info:
            raise BiliError(f"yt-dlp returned no data for {url}")
        return info


def _normalise_bvid(raw_id: Any) -> str | None:
    """多 P 视频的 id 形如 BVxxxx_p1；只接受以合法 BV 号开头的 id。"""
    match = BVID_PATTERN.match(str(raw_id or ""))
    if not match:
        return None
    rest = str(raw_id)[match.end():]
    return match.group(0) if rest == "" or re.fullmatch(r"_p[0-9]+", rest) else None


def _entry(bvid: str, info: dict[str, Any]) -> VideoEntry:
    duration = info.get("duration")
    return VideoEntry(
        bvid=bvid,
        title=info.get("title") or "",
        duration=float(duration) if duration is not None else None,
        view_count=info.get("view_count"),
        upload_date=info.get("upload_date"),
    )


def _strip_query(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def find_output(dest: Path, stem: str) -> Path | None:
    """只认 <stem>.<ext> 这种最终产物；yt-dlp 的 .part、.temp、.f123 等中间文件都排除。"""
    if not dest.exists():
        return None
    pattern = re.compile(rf"{re.escape(stem)}\.[A-Za-z0-9]+")
    candidates = sorted(
        p for p in dest.iterdir()
        if pattern.fullmatch(p.name) and p.suffix not in (".part", ".ytdl") and p.is_file() and p.stat().st_size > 0
    )
    preferred = [p for p in candidates if p.suffix == ".mp4"]
    return next(iter(preferred or candidates), None)
