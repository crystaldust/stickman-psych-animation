"""把用户输入（BV 号、mid、各种 B 站链接）解析成统一的采集目标。"""

import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

BVID_PATTERN = re.compile(r"BV[0-9A-Za-z]{10}")
MID_PATTERN = re.compile(r"[0-9]{1,20}")
VIDEO_HOSTS = frozenset({"www.bilibili.com", "bilibili.com", "m.bilibili.com"})
SPACE_HOST = "space.bilibili.com"
SHORT_HOST = "b23.tv"
ALLOWED_REDIRECT_HOSTS = VIDEO_HOSTS | {SPACE_HOST, SHORT_HOST}
SHORT_LINK_TIMEOUT_SECONDS = 10
MAX_REDIRECTS = 5

Resolver = Callable[[str], str]


class TargetError(ValueError):
    """输入无法识别为 B 站视频或 up 主。"""


@dataclass(frozen=True)
class Target:
    kind: Literal["video", "user"]
    id: str

    @property
    def url(self) -> str:
        if self.kind == "video":
            return f"https://www.bilibili.com/video/{self.id}"
        return f"https://space.bilibili.com/{self.id}/video"


def is_allowed_redirect(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in ALLOWED_REDIRECT_HOSTS


class BiliOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """短链只允许跳转到 B 站的 https 地址，防止被引到内网或第三方主机。"""

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - 覆盖标准库签名
        if not is_allowed_redirect(newurl):
            raise TargetError(f"短链跳转到了非 B 站地址：{newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def resolve_short_link(url: str, opener: Any = None) -> str:
    """跟随 b23.tv 短链的重定向，返回最终地址。"""
    active = opener or urllib.request.build_opener(BiliOnlyRedirectHandler)
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with active.open(request, timeout=SHORT_LINK_TIMEOUT_SECONDS) as response:
            return response.geturl()
    except OSError as exc:  # URLError / HTTPError / 超时都是 OSError
        raise TargetError(f"短链解析失败：{exc}") from exc


def parse_target(raw: str, resolve_short: Resolver | None = None) -> Target:
    text = raw.strip()
    if not text:
        raise TargetError("输入为空")
    if BVID_PATTERN.fullmatch(text):
        return Target("video", text)
    if MID_PATTERN.fullmatch(text):
        return Target("user", text)
    return _parse_url(text, resolve_short or resolve_short_link)


def _parse_url(text: str, resolve_short: Resolver) -> Target:
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise TargetError(f"无法识别的输入：{text}")
    host = (parsed.hostname or "").lower()

    if host == SHORT_HOST:
        if parsed.scheme != "https":
            raise TargetError(f"短链请使用 https：{text}")
        final = resolve_short(text)
        if (urlparse(final).hostname or "").lower() == SHORT_HOST:
            raise TargetError(f"短链没有跳转到 B 站页面：{text}")
        return _parse_url(final, resolve_short)

    if host in VIDEO_HOSTS:
        match = re.match(r"/video/(BV[0-9A-Za-z]{10})(?:/|$)", parsed.path)
        if match:
            return Target("video", match.group(1))

    if host == SPACE_HOST:
        match = re.match(r"/([0-9]{1,20})(?:/|$)", parsed.path)
        if match:
            return Target("user", match.group(1))

    raise TargetError(f"不支持的链接：{text}")
