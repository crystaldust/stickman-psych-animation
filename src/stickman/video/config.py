"""视频流水线的参数。

可调的放在 VideoConfig（命令行参数 > 工作目录里的 video.json > 默认值）；
其余是试点调出来的固定值，改它们会改变成片节奏，所以不开放覆盖。
"""

import json
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from .errors import VideoError

FPS = 30
FRAME_W, FRAME_H = 1920, 1080

# 配音拼接：每段只保留词前 HEAD_KEEP、词后 TAIL_KEEP 的静音，镜头之间再插 SHOT_GAP，
# 所以镜头间实际停顿约 0.75 秒。段内停顿超过上限的从中间裁短。
LEAD = 0.4
SHOT_GAP = 0.55
HEAD_KEEP = 0.05
TAIL_KEEP = 0.15
SENTENCE_PAUSE = 0.6
COMMA_PAUSE = 0.35
WORD_PAUSE = 0.35
TAIL = 1.8

# 字幕：间隔短于 SUB_HOLD 的两条字幕首尾相接，避免闪烁。
SUB_HOLD = 0.6
SUB_EXTRA = 0.3
LAST_SUB_EXTRA = 0.5
MAX_SUB_CHARS = 16
MAX_SUB_CHARS_EN = 42

# 配音先统一到这个响度（EBU R128）再垫 BGM：英文平台音色比中文复刻音色轻约 10 dB，
# 不统一的话 BGM 相对配音的音量就随音色变。中文配音本来就在 -23 左右，统一后基本不变。
VOICE_LUFS = -23
VOICE_TRUE_PEAK = -2
BGM_GAIN_DB = -21
BGM_FADE_IN = 2.0
BGM_FADE_OUT = 3.0
# 画面效果的版本：改了会改变片段画面的代码（质感、摆动、分层合成）就加一。
# 成片清单按它记每个镜头的画面指纹，英文版据此确认中文版是用同一版效果渲染的。
# 2：修了发光；3：去掉结尾的 CRT 关机动画。
LOOK_VERSION = 3

ENCODER = "h264_videotoolbox"
TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/create"
IMAGE_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/images/generations"
IMAGE_SIZE = "1920x1080"
# 可选的出图模型，第一个是默认：pro 画面更稳；flash 更快更便宜（用户要求加入，可用 --image-model 或 video.json 切换）。
IMAGE_MODELS = ("doubao-seedream-5-0-pro-260628", "doubao-seedream-5-0-flash-260915")
# 出图接口返回的下载链接只接受这些域名（及其子域名）。
IMAGE_HOSTS = ("volces.com", "volccdn.com")
MAX_IMAGE_BYTES = 20 * 1024 * 1024
TTS_TIMEOUT_S = 300
IMAGE_TIMEOUT_S = 240
RETRIES = 3
RETRY_BASE_S = 5
KEYCHAIN_IMAGE = "ark-api-key"
KEYCHAIN_TTS = "volc-tts-key"

TTS_STYLE = "语速平稳，沉稳、克制，像深夜的旁白，句与句之间停顿不要太长。安静的室内，没有背景音乐，没有任何音效。"
TTS_STYLE_EN = ("Calm and restrained, like a late-night narrator; steady pace, pauses between sentences not too long. "
                "Quiet room, no music, no sound effects. ")
STYLE_PROMPT = (
    "参考图中火柴人的角色造型和画风：超大的白色圆头、大圆圈小黑点眼睛、简单眉毛、粗细不均的黑色马克笔描边、"
    "单线条身体四肢。粗糙的手绘火柴人涂鸦，扁平，无阴影无渐变，像 MS Paint 随手画的。16:9 横幅，中景构图："
    "火柴人在画面中只占约三分之一高度，位于画面中部，四周留出空间；画面下方五分之一留空给字幕。"
    "背景是纯色平涂的{bg}，配极少的简笔涂鸦道具。不要文字，不要写实，不要3D，不要水彩，不要光影渲染。画面内容："
)

# 分层镜头里单独生成的人物、道具、风景条：只要画风，不要 STYLE_PROMPT 里的构图和背景要求。
LAYER_STYLE = (
    "参考图中火柴人的角色造型和画风：超大的白色圆头、大圆圈小黑点眼睛、简单眉毛、粗细不均的黑色马克笔描边、"
    "单线条身体四肢。粗糙的手绘涂鸦，扁平，无阴影无渐变，像 MS Paint 随手画的。16:9 横幅。"
    "不要文字，不要写实，不要3D，不要水彩，不要光影渲染。画面内容："
)

OVERRIDE_FILE = "video.json"
MAX_WORKERS = 16


@dataclass(frozen=True)
class VideoConfig:
    ref_image: str = "data/pilot/02-doodle.jpg"
    speaker: str = "S_VQ18Xi2i2"
    speaker_en: str = "en_male_hades_uranus_bigtts"
    bgm: str = "data/pilot/bgm/mangguohuami-from3.5.mp3"
    speech_rate: int = 10
    speech_rate_en: int = 5  # 英文用 10 太赶，用户在豆包网页上试听选定 5
    image_model: str = IMAGE_MODELS[0]
    tts_model: str = "seed-audio-1.0"
    img_workers: int = 8
    tts_workers: int = 4
    render_workers: int = 6
    video_bitrate: str = "4M"


_TYPES = {f.name: type(getattr(VideoConfig(), f.name)) for f in fields(VideoConfig)}


def _checked(values: dict[str, Any], origin: str) -> dict[str, Any]:
    unknown = sorted(set(values) - set(_TYPES))
    if unknown:
        raise VideoError(f"{origin} 里有不认识的参数：{', '.join(unknown)}")
    for name, value in values.items():
        expected = _TYPES[name]
        if type(value) is not expected:  # bool 是 int 的子类，这里要严格比较
            raise VideoError(f"{origin} 的 {name} 应该是 {expected.__name__}")
        if name.endswith("_workers") and not 1 <= value <= MAX_WORKERS:
            raise VideoError(f"{origin} 的 {name} 应在 1–{MAX_WORKERS} 之间")
        if name == "image_model" and value not in IMAGE_MODELS:
            raise VideoError(f"{origin} 的 image_model 只能是 {' / '.join(IMAGE_MODELS)}")
    return values


def load_config(work: Path, overrides: dict[str, Any] | None = None) -> VideoConfig:
    """默认值 ← <work>/video.json ← 命令行参数（值为 None 的忽略）。"""
    config = VideoConfig()
    path = work / OVERRIDE_FILE
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise VideoError(f"{path} 不是合法的 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise VideoError(f"{path} 应该是一个 JSON 对象")
        config = replace(config, **_checked(data, str(path)))
    given = {k: v for k, v in (overrides or {}).items() if v is not None}
    return replace(config, **_checked(given, "命令行参数"))
