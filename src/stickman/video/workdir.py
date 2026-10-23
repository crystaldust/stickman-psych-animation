"""一条视频的工作目录：data/videos/<slug>/ 下各产物的固定位置，以及路径安全检查。"""

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import VideoError

_SAFE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def _safe(value: str, kind: str) -> str:
    if not _SAFE_NAME.fullmatch(value):
        raise VideoError(f"{kind}「{value}」不合法：只能用小写字母、数字、- 和 _，且以字母或数字开头，最长 64 个字符")
    return value


def safe_slug(value: str) -> str:
    return _safe(value, "目录名")


def safe_id(value: str) -> str:
    return _safe(value, "镜头 id")


def inside_project(value: str | Path) -> Path:
    """要写入的路径必须在项目目录（当前目录）内，避免被引导去覆盖别处的文件。"""
    root = Path.cwd().resolve()
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root):
        raise VideoError(f"路径必须在项目目录内：{value}")
    return resolved


LANG_CODES = ("zh", "en")  # 和 lang.LANGS 一致；这里只管路径，不依赖 lang 模块
WIDE, TALL = "16x9", "9x16"  # 横版是主版本；竖屏是抖音版
FORMAT_CODES = (WIDE, TALL)


@dataclass(frozen=True)
class WorkDir:
    """lang 决定旁白、配音、字幕、片段和成片的位置；shots.json、图和图层所有语言共用。

    fmt 决定画幅：竖屏版共用同一语言横版的配音和发布信息，只有字幕、片段、清单和成片另存。
    中文横版（主版本）沿用原来的文件名，英文加 .en / _en 后缀，竖屏再加 .9x16 / _9x16。
    """
    root: Path
    lang: str = "zh"
    fmt: str = WIDE

    def __post_init__(self) -> None:
        if self.lang not in LANG_CODES:
            raise VideoError(f"不支持的语言「{self.lang}」，应该是 {' / '.join(LANG_CODES)}")
        if self.fmt not in FORMAT_CODES:
            raise VideoError(f"不支持的画幅「{self.fmt}」，应该是 {' / '.join(FORMAT_CODES)}")

    @property
    def label(self) -> str:
        """报错里怎么称呼这个版本：en 版、竖屏版、en 竖屏版。"""
        lang = "" if self.lang == LANG_CODES[0] else f"{self.lang} "
        shape = "" if self.fmt == WIDE else "竖屏"
        return f"{lang}{shape}版" if lang or shape else "中文版"

    def _tags(self, per_format: bool) -> list[str]:
        return ([] if self.lang == LANG_CODES[0] else [self.lang]) + (
            [self.fmt] if per_format and self.fmt != WIDE else [])

    def _file(self, stem: str, ext: str, per_format: bool = False) -> Path:
        return self.root / (".".join([stem, *self._tags(per_format)]) + ext)

    def _dir(self, name: str, per_format: bool = False) -> Path:
        return self.root / "_".join([name, *self._tags(per_format)])

    @property
    def narration(self) -> Path:
        return self._file("narration", ".txt")

    @property
    def shots(self) -> Path:
        return self.root / "shots.json"

    @property
    def img_dir(self) -> Path:
        return self.root / "img"

    @property
    def rejected_dir(self) -> Path:
        return self.root / "img_rejected"

    @property
    def voice_dir(self) -> Path:
        return self._dir("voice")

    @property
    def trim_dir(self) -> Path:
        return self._dir("voice_trim")

    @property
    def clip_dir(self) -> Path:
        return self._dir("clips", per_format=True)

    @property
    def voice_wav(self) -> Path:
        return self._file("voice", ".wav")

    @property
    def subs(self) -> Path:
        return self._file("subs", ".ass", per_format=True)

    @property
    def srt(self) -> Path:
        return self._file("subs", ".srt")

    @property
    def clip_list(self) -> Path:
        return self._file("clips", ".txt", per_format=True)

    @property
    def manifest(self) -> Path:
        return self._file("render", ".json", per_format=True)

    @property
    def timings(self) -> Path:
        return self._file("timings", ".json", per_format=True)

    @property
    def layers_dir(self) -> Path:
        return self.root / "layers"

    @property
    def publish(self) -> Path:
        """Claude 写的发布信息：标题、简介、出处、封面用哪个镜头和什么字。"""
        return self._file("publish", ".json")

    @property
    def title(self) -> Path:
        return self._file("title", ".txt")

    @property
    def description(self) -> Path:
        return self._file("description", ".txt")

    @property
    def headline(self) -> Path:
        """竖屏版顶部常驻的封面字：透明底 png，渲染时生成。"""
        return self._file("headline", ".png", per_format=True)

    @property
    def package_tmp(self) -> Path:
        return self._dir("package_tmp")

    def thumbnail(self, layout: str) -> Path:
        """封面：16x9 是 thumbnail.jpg，其他比例带后缀，比如 thumbnail-3x4.jpg。"""
        return self._file("thumbnail" if layout == "16x9" else f"thumbnail-{_safe(layout, '封面比例')}", ".jpg")

    def image(self, shot_id: str) -> Path:
        return self.img_dir / f"{safe_id(shot_id)}.jpg"

    def layer(self, shot_id: str, name: str) -> Path:
        """分层镜头的一层：layers/<镜头>/<图层>.jpg。"""
        return self.layers_dir / safe_id(shot_id) / f"{_safe(name, '图层名')}.jpg"

    def voice(self, shot_id: str) -> Path:
        return self.voice_dir / f"{safe_id(shot_id)}.mp3"

    def voice_meta(self, shot_id: str) -> Path:
        return self.voice_dir / f"{safe_id(shot_id)}.mp3.json"

    def trimmed(self, shot_id: str) -> Path:
        return self.trim_dir / f"{safe_id(shot_id)}.wav"

    def clip(self, shot_id: str) -> Path:
        return self.clip_dir / f"{safe_id(shot_id)}.mp4"

    def default_output(self) -> Path:
        return self._file(self.root.name, ".mp4", per_format=True)
