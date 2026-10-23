"""旁白语言。中文是主版本：分镜、出图都以它为准。

英文版共用中文版的图和 shots.json，只换旁白（narration.en.txt，一行对应中文一行）、配音、字幕和成片。
各语言的差别都收在 Lang 里：怎么数「有时间戳的字」、怎么送去配音、怎么切字幕、用哪个音色和字体。
"""

from collections.abc import Callable
from dataclasses import dataclass

from .config import TTS_STYLE, TTS_STYLE_EN
from .subtitles import SUB_STYLE, SUB_STYLE_9X16, SUB_STYLE_EN, SUB_STYLE_EN_9X16
from .text import apply_homophones, norm, norm_en, split_sub, split_sub_en, spoken, spoken_en


def _same(text: str) -> str:
    return text


@dataclass(frozen=True)
class Lang:
    code: str
    norm: Callable[[str], str]  # 有时间戳的字符：中文按字，英文按字母（词的时间摊到字母上）
    canon: Callable[[str], str]  # 比对配音内容前对返回文本的修正（中文：配音有时返回原词而不是同音字）
    spoken: Callable[[str], str]  # 送去配音的文本
    split_sub: Callable[[str], list[str]]
    joiner: str  # 一个镜头的几行旁白拼成一段配音时用的分隔
    tts_prompt: str  # 含 {text}
    sub_style: str  # ASS 的 Style 行
    sub_style_9x16: str  # 竖屏版的 Style 行
    speaker_field: str  # VideoConfig 里这个语言音色的字段名
    rate_field: str  # VideoConfig 里这个语言语速的字段名

    def prompt(self, text: str) -> str:
        return self.tts_prompt.replace("{text}", self.spoken(text))


ZH = Lang(code="zh", norm=norm, canon=apply_homophones, spoken=spoken, split_sub=split_sub, joiner="",
          tts_prompt=TTS_STYLE + "他一字不差地说：“{text}”", sub_style=SUB_STYLE, sub_style_9x16=SUB_STYLE_9X16,
          speaker_field="speaker", rate_field="speech_rate")
EN = Lang(code="en", norm=norm_en, canon=_same, spoken=spoken_en, split_sub=split_sub_en, joiner=" ",
          tts_prompt=TTS_STYLE_EN + 'He says, word for word: "{text}"', sub_style=SUB_STYLE_EN,
          sub_style_9x16=SUB_STYLE_EN_9X16, speaker_field="speaker_en", rate_field="speech_rate_en")
LANGS = {lang.code: lang for lang in (ZH, EN)}
PRIMARY = ZH.code
