"""旁白文本的几种形态：送去配音的、带时间戳的、上字幕的。"""

import re

from .config import MAX_SUB_CHARS

# 配音模型会读错的词 → 同音字，只用在配音上，字幕保留原文。
# ta 只替换单独出现的 ta，英文单词里的（data、start）不动。
HOMOPHONES = (
    (re.compile(r"(?<![A-Za-z])ta(?![A-Za-z])"), "他"),
    (re.compile("倒放"), "到放"),
)
_SUB_BREAK = re.compile(r"(?<=[，。：！？、；])")
_SPOKEN_CHAR = re.compile(r"[一-鿿0-9A-Za-z]")


def apply_homophones(text: str) -> str:
    for pattern, dst in HOMOPHONES:
        text = pattern.sub(dst, text)
    return text


def spoken(text: str) -> str:
    """送去配音的文本：同音字替换；去掉「」、把 “” 换成 ‘’——「」会让返回的词级时间戳错位。"""
    text = apply_homophones(text).replace("「", "").replace("」", "")
    return text.replace("“", "‘").replace("”", "’")


def norm(text: str) -> str:
    """会拿到词级时间戳的字符：汉字和字母数字，按配音时的读法（已做同音字替换）。"""
    return "".join(_SPOKEN_CHAR.findall(apply_homophones(text)))


def split_sub(text: str) -> list[str]:
    """一行旁白切成字幕：按标点断开，过长的再按 MAX_SUB_CHARS 硬切，去掉句末标点。"""
    out = []
    for chunk in (c for c in _SUB_BREAK.split(text) if c.strip()):
        rest = chunk.strip().rstrip("，。、；")
        while len(rest) > MAX_SUB_CHARS:
            out.append(rest[:MAX_SUB_CHARS])
            rest = rest[MAX_SUB_CHARS:]
        if rest:
            out.append(rest)
    return out
