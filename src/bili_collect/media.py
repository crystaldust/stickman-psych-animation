"""用 ffmpeg 把下载的音频流转成 Whisper 需要的 16kHz 单声道 wav。"""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

SAMPLE_RATE = "16000"

Runner = Callable[..., subprocess.CompletedProcess]


class MediaError(RuntimeError):
    """ffmpeg 处理失败。"""


def extract_audio(src: Path, dst: Path, runner: Runner = subprocess.run) -> Path:
    if not src.exists():
        raise FileNotFoundError(src)
    if dst.exists():
        return dst

    # 先写 .part 再原子改名：被中断时不会留下会被当成已完成的半截 wav
    part = dst.with_name(dst.name + ".part")
    part.unlink(missing_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src.absolute()), "-vn", "-ac", "1", "-ar", SAMPLE_RATE, "-f", "wav",
        str(part.absolute()),
    ]
    try:
        try:
            result = runner(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError as exc:
            raise MediaError("找不到 ffmpeg，请先安装：brew install ffmpeg") from exc
        if result.returncode != 0:
            raise MediaError(f"ffmpeg failed for {src.name}: {result.stderr.strip()}")
        if not part.exists():
            raise MediaError(f"ffmpeg produced no output for {src.name}")
        os.replace(part, dst)
    finally:
        part.unlink(missing_ok=True)
    return dst
