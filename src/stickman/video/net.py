"""调火山接口用的网络小工具：POST JSON、重试、下载图片。

- 接口返回的错误正文可能回显请求头，所以进错误信息前先把 key 替换掉，再截断。
- POST 不跟随重定向：urllib 跟随时会把带 key 的请求头原样发给新地址。
- 下载图片时每一跳重定向都重新检查 https 和域名白名单，内容必须是 JPEG / PNG。
- 付费请求失败后会重试；如果服务端其实已经处理了，重试会重复计费——接口没有幂等键，只能接受。
"""

import http.client
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlparse

from .config import IMAGE_TIMEOUT_S, RETRIES, RETRY_BASE_S
from .errors import VideoError

RETRY_CODES = frozenset({429, 500, 502, 503, 504})
MAX_BODY_CHARS = 300
IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")
_NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)

Opener = Callable[..., Any]
T = TypeVar("T")


class HttpError(VideoError):
    """接口或网络错误；retryable 表示值得重试（限流、服务端错误、网络抖动）。"""

    def __init__(self, label: str, detail: str, retryable: bool):
        super().__init__(f"{label}：{detail}")
        self.retryable = retryable


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None  # urllib 随后把 3xx 当作 HTTPError 抛出


class _AllowlistRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_hosts: tuple[str, ...]):
        super().__init__()
        self.allow_hosts = allow_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        check_url(newurl, self.allow_hosts, "图片重定向")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_post_opener = urllib.request.build_opener(_NoRedirect).open


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def _error_body(err: urllib.error.HTTPError, secret: str) -> str:
    try:
        return redact(err.read(MAX_BODY_CHARS * 4).decode(errors="replace"), secret)[:MAX_BODY_CHARS]
    except (OSError, ValueError, http.client.HTTPException):
        return ""
    finally:
        err.close()


def post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: float, secret: str, label: str,
              opener: Opener = _post_opener) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={**headers, "Content-Type": "application/json"})
    try:
        with opener(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as err:
        raise HttpError(label, f"HTTP {err.code} {_error_body(err, secret)}", err.code in RETRY_CODES) from err
    except _NETWORK_ERRORS as err:
        raise HttpError(label, f"网络错误：{redact(str(err), secret)[:MAX_BODY_CHARS]}", True) from err
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VideoError(f"{label}：返回的不是 JSON") from exc
    if not isinstance(data, dict):
        raise VideoError(f"{label}：返回的 JSON 不是对象")
    return data


def with_retry(call: Callable[[], T], label: str, sleep: Callable[[float], None] = time.sleep) -> T:
    for attempt in range(RETRIES + 1):
        try:
            return call()
        except HttpError as err:
            if not err.retryable or attempt == RETRIES:
                raise
            wait = RETRY_BASE_S * 2 ** attempt
            print(f"  {err}，{wait} 秒后重试", flush=True)
            sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover


def check_url(url: str, allow_hosts: tuple[str, ...], label: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.scheme != "https" or not any(host == h or host.endswith("." + h) for h in allow_hosts):
        raise VideoError(f"{label}：链接 {parsed.scheme}://{host} 不在允许的域名里（{', '.join(allow_hosts)}）")


def download(url: str, dest: Path, allow_hosts: tuple[str, ...], max_bytes: int, label: str,
             opener: Opener | None = None) -> None:
    """下载图片到 dest：https + 白名单域名（含每次重定向）+ JPEG/PNG + 不超过 max_bytes；先写 .part 再改名。"""
    check_url(url, allow_hosts, label)
    open_url = opener or urllib.request.build_opener(_AllowlistRedirect(allow_hosts)).open
    try:
        with open_url(url, timeout=IMAGE_TIMEOUT_S) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if not ctype.startswith("image/"):
                raise VideoError(f"{label}：下载到的不是图片（{ctype[:40]}）")
            data = resp.read(max_bytes + 1)
    except urllib.error.HTTPError as err:
        err.close()
        raise HttpError(label, f"下载图片 HTTP {err.code}", err.code in RETRY_CODES) from err
    except _NETWORK_ERRORS as err:
        raise HttpError(label, f"下载图片失败：{type(err).__name__}", True) from err
    if len(data) > max_bytes:
        raise VideoError(f"{label}：图片超过 {max_bytes} 字节")
    if not data.startswith(IMAGE_MAGIC):
        raise VideoError(f"{label}：下载到的内容不是 JPEG / PNG")
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part.write_bytes(data)
    os.replace(part, dest)
