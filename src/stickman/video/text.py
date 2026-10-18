"""旁白文本的几种形态：送去配音的、带时间戳的、上字幕的。"""

import re

from .config import MAX_SUB_CHARS, MAX_SUB_CHARS_EN

# 配音模型会读错的词 → 同音字，只用在配音上，字幕保留原文。
# ta 只替换单独出现的 ta，英文单词里的（data、start）不动。
HOMOPHONES = (
    (re.compile(r"(?<![A-Za-z])ta(?![A-Za-z])"), "他"),
    (re.compile("倒放"), "到放"),
)
# 在标点后断开；标点后面紧跟着的后引号、后书名号、后括号（可能叠好几个）都跟着这一条，免得跑到下一条字幕。
_SUB_BREAK = re.compile(r"[，。：！？、；][”」』》）]*")
# 硬切字幕时不可拆的单位：一个书名（《…》），或一个字连同贴着它的前引号、后引号。
_SUB_ATOM = re.compile(r"[“「『（]*(?:《[^《》]*》|.)[”」』）]*")
_SUB_TRAIL = re.compile(r"[，。、；]+(?=[”」』》）]*$)")
_SPOKEN_CHAR = re.compile(r"[一-鿿0-9A-Za-z]")
_EN_SPOKEN = re.compile(r"[a-z0-9]")
# 断句：句末标点（或分号、冒号）后面跟着大写开头的词才断，「wait... and then」这类不断。
# 缩写（Mr. Smith）分不出来，所以译文里不要用缩写。
_EN_SENTENCE = re.compile(r"(?<=[.!?…;:])\s+(?=['‘(]?[A-Z])")
_EN_CLAUSE = re.compile(r"(?<=,)\s+")


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


def _hard_split(text: str) -> list[str]:
    """超过 MAX_SUB_CHARS 的一段按字数硬切，但不拆书名、不让引号落单；书名本身超长才拆开它。"""
    out, line = [], ""
    for atom in _SUB_ATOM.findall(text):
        for piece in (atom if len(atom) > MAX_SUB_CHARS else (atom,)):
            if line and len(line) + len(piece) > MAX_SUB_CHARS:
                out.append(line)
                line = ""
            line += piece
    return out + [line] if line else out


def split_sub(text: str) -> list[str]:
    """一行旁白切成字幕：按标点断开，过长的再按 MAX_SUB_CHARS 硬切，去掉句末标点（后引号、后书名号留着）。"""
    out = []
    for chunk in (c for c in _SUB_BREAK.sub(lambda m: m.group() + "\n", text).split("\n") if c.strip()):
        out += _hard_split(_SUB_TRAIL.sub("", chunk.strip()))
    return out


# ---------- 英文：配音按词返回时间戳，这里把每个词摊到它的字母上，后面的流程就和中文按字一样。


def norm_en(text: str) -> str:
    """会拿到时间戳的字符：小写字母和数字（撇号、连字符、空格、标点都不算）。"""
    return "".join(_EN_SPOKEN.findall(text.lower()))


def spoken_en(text: str) -> str:
    """送去配音的英文：双引号换成单引号，免得提前关掉提示词里包住旁白的那对双引号。"""
    return text.replace('"', "'").replace("“", "'").replace("”", "'")


def _greedy(words: list[str], width: int) -> list[str]:
    out, line = [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    return out + [line]


def _wrap_words(text: str, limit: int) -> list[str]:
    """超长的一句按单词断成长度相近的几行，不拆单词：取能凑成最少行数的最小行宽。"""
    if len(text) <= limit:
        return [text]
    words, pieces = text.split(), -(-len(text) // limit)
    for width in range(len(text) // pieces, limit + 1):
        lines = _greedy(words, width)
        if len(lines) <= pieces:
            return lines
    return _greedy(words, limit)


def _pack(parts: list[str], limit: int) -> list[str]:
    """把逗号分开的短句尽量拼回一行（不超过 limit），英文字幕一个逗号一条会碎得看不过来。"""
    out: list[str] = []
    for part in parts:
        if out and len(out[-1]) + 1 + len(part) <= limit:
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return out


def split_sub_en(text: str) -> list[str]:
    """英文旁白切成字幕：一句一条；长句先按逗号、再按单词折成几行；去掉末尾的逗号句号（保留问号叹号）。"""
    out = []
    for sentence in _EN_SENTENCE.split(text.strip()):
        sentence = sentence.strip()
        if len(sentence.rstrip(",.;:")) <= MAX_SUB_CHARS_EN:
            pieces = [sentence]
        else:
            pieces = [w for p in _pack(_EN_CLAUSE.split(sentence), MAX_SUB_CHARS_EN)
                      for w in _wrap_words(p.rstrip(","), MAX_SUB_CHARS_EN)]
        out += [p.rstrip(",.;:") for p in pieces if p.rstrip(",.;:")]
    return out
