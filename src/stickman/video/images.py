"""火山方舟 Seedream 出图：每个镜头一张，带角色参考图保持画风一致。"""

import base64
import time
from collections.abc import Callable
from pathlib import Path

from .config import IMAGE_ENDPOINT, IMAGE_HOSTS, IMAGE_SIZE, IMAGE_TIMEOUT_S, MAX_IMAGE_BYTES, VideoConfig
from .errors import VideoError
from .net import IMAGE_MAGIC, download, post_json, with_retry

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

Post = Callable[..., dict]
Fetch = Callable[..., None]


def ref_data_url(path: Path) -> str:
    mime = _MIME.get(path.suffix.lower())
    if mime is None:
        raise VideoError(f"参考图只支持 jpg / png：{path}")
    if not path.is_file():
        raise VideoError(f"找不到参考图：{path}")
    data = path.read_bytes()
    if not data.startswith(IMAGE_MAGIC):
        raise VideoError(f"参考图不是 JPEG / PNG：{path}")
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def image_body(prompt: str, ref_url: str, config: VideoConfig) -> dict:
    return {"model": config.image_model, "prompt": prompt, "image": ref_url, "size": IMAGE_SIZE,
            "response_format": "url", "watermark": False}


def _image_url(data: dict, label: str) -> str:
    items = data.get("data")
    url = items[0].get("url") if isinstance(items, list) and items and isinstance(items[0], dict) else None
    if not isinstance(url, str):
        raise VideoError(f"{label}：返回里没有图片链接")
    return url


def generate_image(prompt: str, ref_url: str, key: str, config: VideoConfig, out: Path,
                   post: Post = post_json, fetch: Fetch = download,
                   sleep: Callable[[float], None] = time.sleep, label: str = "出图") -> None:
    """生成一张图并下载到 out。生成和下载分开重试：下载失败不该再花钱重新生成。"""
    body = image_body(prompt, ref_url, config)
    data = with_retry(lambda: post(IMAGE_ENDPOINT, body, {"Authorization": f"Bearer {key}"},
                                   IMAGE_TIMEOUT_S, key, label), label, sleep)
    url = _image_url(data, label)
    with_retry(lambda: fetch(url, out, IMAGE_HOSTS, MAX_IMAGE_BYTES, label), label, sleep)
