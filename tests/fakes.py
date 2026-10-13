"""测试替身：yt-dlp 的 YoutubeDL，以及 pipeline 用的视频源客户端。"""

from pathlib import Path
from typing import Any

from yt_dlp.utils import DownloadError

from bili_collect.bili import BiliError
from bili_collect.models import VideoEntry

MID = "3546974439607171"


class FakeYDL:
    """按 URL 返回预置 info，记录传入参数，download=True 时按 outtmpl 生成文件。"""

    def __init__(self, responses, created, files=None, error=None, error_cls=DownloadError):
        self.responses = responses
        self.created = created
        self.files = files or {}
        self.error = error
        self.error_cls = error_cls
        self.params: dict[str, Any] | None = None
        self.cookie_snapshot: str | None = None

    def __call__(self, params):
        self.params = params
        self.created.append(params)
        if "cookiefile" in params:
            self.cookie_snapshot = Path(params["cookiefile"]).read_text(encoding="utf-8")
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if self.error:
            raise self.error_cls(self.error)
        if download:
            template = self.params["outtmpl"].replace("%(ext)s", "{ext}").replace("%%", "%")
            for ext in self.files.get(url, []):
                path = Path(template.format(ext=ext))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"media")
        return self.responses.get(url)


def make_factory(responses=None, files=None, error=None, error_cls=DownloadError):
    created: list[dict[str, Any]] = []
    fake = FakeYDL(responses or {}, created, files=files, error=error, error_cls=error_cls)
    return fake, created


def meta_for(bvid: str, title: str = "标题", uploader_id: str | None = MID) -> dict[str, Any]:
    meta = {"id": bvid, "title": title, "duration": 60.0, "upload_date": "20251021"}
    if uploader_id:
        meta["uploader_id"] = uploader_id
    return meta


class FakeClient:
    """满足 pipeline.VideoSource 协议的内存实现。"""

    def __init__(
        self,
        listing=(),
        failing=(),
        error_cls=BiliError,
        uploader_id: str | None = MID,
        subtitles=None,
        subtitle_error=None,
    ):
        self.subtitles = subtitles
        self.subtitle_error = subtitle_error
        self.listing = list(listing)
        self.failing = set(failing)
        self.error_cls = error_cls
        self.uploader_id = uploader_id
        self.calls: list[tuple] = []

    def list_user_videos(self, mid: str, limit: int | None = None) -> list[VideoEntry]:
        self.calls.append(("list", mid, limit))
        return self.listing

    def fetch_meta(self, bvid: str) -> dict[str, Any]:
        self.calls.append(("meta", bvid))
        if bvid in self.failing:
            raise self.error_cls(f"boom {bvid}")
        return meta_for(bvid, uploader_id=self.uploader_id)

    def fetch_subtitles(self, bvid: str):
        self.calls.append(("subtitles", bvid))
        if self.subtitle_error:
            raise self.subtitle_error
        return self.subtitles

    @staticmethod
    def entry_from_meta(info: dict[str, Any]) -> VideoEntry:
        return VideoEntry(info["id"], info["title"], duration=info["duration"], upload_date=info["upload_date"])

    def download_audio(self, bvid: str, dest: Path) -> Path:
        self.calls.append(("audio", bvid))
        path = dest / "source.m4a"
        path.write_bytes(b"a")
        return path

    def download_video(self, bvid: str, dest: Path) -> Path:
        self.calls.append(("video", bvid))
        path = dest / "video.mp4"
        path.write_bytes(b"v")
        return path
