"""视频流水线的测试替身：假的 ffmpeg/ffprobe、配音和出图客户端，以及构造词级时间戳的工具。"""

import json
from pathlib import Path

from stickman.video.errors import VideoError
from stickman.video.text import norm, spoken
from stickman.video.tts import TtsResult


class FakeRunner:
    """记录每条命令；ffmpeg 命令会创建输出文件（最后一个参数），ffprobe 按路径返回预置的时长或帧数。"""

    def __init__(self, durations=None, frames=None, default_duration=2.0, fail_on=None, ppm_pixels=None):
        self.calls: list[list[str]] = []
        self.ppm_pixels = ppm_pixels  # 探测图层范围时写出的 .ppm：默认 2x1 全黑，即内容铺满整个画面
        self.durations = dict(durations or {})
        self.frames = dict(frames or {})
        self.default_duration = default_duration
        self.fail_on = fail_on  # 子串：命令里出现它就失败一次

    def __call__(self, cmd):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        if self.fail_on and any(self.fail_on in c for c in cmd):
            self.fail_on = None
            raise VideoError("ffmpeg 失败：boom")
        if cmd[0] == "ffprobe":
            path, name = cmd[-1], Path(cmd[-1]).name
            if "-count_frames" in cmd:
                return f"{self.frames.get(path, self.frames.get(name, 0))}\n"
            return f"{self.durations.get(path, self.durations.get(name, self.default_duration))}\n"
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.suffix == ".ppm":
            pixels = self.ppm_pixels or [(0, 0, 0), (0, 0, 0)]
            out.write_bytes(ppm(len(pixels), 1, pixels))
            return ""
        out.write_bytes(b"media:" + " ".join(cmd).encode())
        return ""

    def ffmpeg_calls(self):
        return [c for c in self.calls if c[0] == "ffmpeg"]


def word_meta(text: str, start_ms: int = 100, step_ms: int = 200, pauses=None, source_text=None) -> dict:
    """按字生成 seed-audio 风格的字幕元数据；pauses={字序号: 额外停顿毫秒}，标点作为独立 token。"""
    pauses = pauses or {}
    words, t, k = [], start_ms, 0
    for ch in text:
        if not norm(ch):
            words.append({"text": ch, "start_time": t, "end_time": t})
            continue
        t += pauses.get(k, 0)
        words.append({"text": ch, "start_time": t, "end_time": t + step_ms})
        t += step_ms
        k += 1
    meta = {"original_duration": t / 1000 + 0.3, "subtitle": {"sentences": [{"text": text, "words": words}]}}
    if source_text is not None:
        meta["source_text"] = source_text
    return meta


class FakeTts:
    """按 seed-audio 的格式返回逐字时间戳；fail_texts 里的文本第一次调用时失败。"""

    def __init__(self, fail_texts=()):
        self.texts: list[str] = []
        self.fail_texts = set(fail_texts)

    def __call__(self, text, key, config, label=""):
        self.texts.append(text)
        if text in self.fail_texts:
            self.fail_texts.discard(text)
            raise VideoError(f"{label}：HTTP 500 boom")
        meta = word_meta(spoken(text), source_text=text)
        return TtsResult(b"mp3", meta, meta["original_duration"])


def ppm(width: int, height: int, pixels) -> bytes:
    return f"P6\n{width} {height}\n255\n".encode() + bytes(c for px in pixels for c in px)


class FakeImages:
    """按 label 的最后一段记录提示词：镜头是 s01，图层是 s03:fg。"""

    def __init__(self, fail_ids=()):
        self.prompts: dict[str, str] = {}
        self.fail_ids = set(fail_ids)

    def __call__(self, prompt, ref_url, key, config, out, label=""):
        sid = label.split()[-1] if label else Path(out).stem
        self.prompts[sid] = prompt
        if sid in self.fail_ids:
            self.fail_ids.discard(sid)
            raise VideoError(f"{label}：HTTP 500 boom")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"jpg-" + sid.encode() + str(len(self.prompts)).encode())


class FakeSecrets:
    def __init__(self):
        self.services: list[str] = []

    def __call__(self, service):
        self.services.append(service)
        return f"key-{service}"


def write_voice(work, sid: str, text: str, **kwargs) -> None:
    work.voice_dir.mkdir(parents=True, exist_ok=True)
    work.voice(sid).write_bytes(b"mp3")
    work.voice_meta(sid).write_text(json.dumps(word_meta(text, **kwargs), ensure_ascii=False), encoding="utf-8")
