"""豆包 seed-audio 配音：一个镜头一段，带词级时间戳（字幕和裁停顿都靠它）。"""

import base64
import binascii
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import TTS_ENDPOINT, TTS_STYLE, TTS_TIMEOUT_S, VideoConfig
from .errors import VideoError
from .net import post_json, with_retry
from .text import spoken
from .timeline import word_tokens

Post = Callable[..., dict]
# 落盘的元数据只留用得到的字段，接口以后多返回什么都不存。
KEPT_FIELDS = ("duration", "original_duration", "subtitle")


@dataclass(frozen=True)
class TtsResult:
    audio: bytes
    meta: dict  # 接口返回去掉音频后的部分（含词级时间戳），外加 source_text
    billed_s: float


def tts_body(text: str, config: VideoConfig) -> dict:
    return {
        "model": config.tts_model,
        "text_prompt": f"{TTS_STYLE}他一字不差地说：“{spoken(text)}”",
        "references": [{"speaker": config.speaker}],
        "audio_config": {"format": "mp3", "sample_rate": 44100, "enable_subtitle": True,
                         "speech_rate": config.speech_rate},
    }


def _parse(data: dict, text: str, label: str) -> TtsResult:
    audio_b64 = data.get("audio")
    if not isinstance(audio_b64, str) or not audio_b64:
        raise VideoError(f"{label}：返回里没有音频")
    try:
        audio = base64.b64decode(audio_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VideoError(f"{label}：音频不是合法的 base64") from exc
    word_tokens(data, label)
    billed = data.get("original_duration")
    if type(billed) not in (int, float):
        raise VideoError(f"{label}：返回里没有计费时长 original_duration")
    meta = {k: data[k] for k in KEPT_FIELDS if k in data}
    return TtsResult(audio, {**meta, "source_text": text}, float(billed))


def synthesize(text: str, key: str, config: VideoConfig, post: Post = post_json,
               sleep: Callable[[float], None] = time.sleep, label: str = "配音") -> TtsResult:
    body = tts_body(text, config)
    data = with_retry(lambda: post(TTS_ENDPOINT, body, {"X-Api-Key": key}, TTS_TIMEOUT_S, key, label), label, sleep)
    return _parse(data, text, label)


def save_voice(result: TtsResult, mp3: Path, meta_path: Path) -> None:
    """两份文件都先写 .part 再改名；音频最后落盘，所以「音频在」就意味着元数据也在。"""
    mp3.parent.mkdir(parents=True, exist_ok=True)
    mp3_part, meta_part = mp3.with_name(mp3.name + ".part"), meta_path.with_name(meta_path.name + ".part")
    mp3_part.write_bytes(result.audio)
    meta_part.write_text(json.dumps(result.meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(meta_part, meta_path)
    os.replace(mp3_part, mp3)
