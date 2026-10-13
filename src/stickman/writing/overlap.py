"""草稿查重：找出草稿里和已采集文案连续重合的片段，防止无意中照搬参考频道。

比较前去掉标点和空白（换个标点照抄也能查出来）；引号里的内容视为引用，不参与比较。
"""

import re
from dataclasses import dataclass

DEFAULT_MIN_CHARS = 15
CHARS_PER_SECOND = 7  # 参考频道实测语速
TARGET_CHARS = (650, 1300)  # 风格指南：1.5–3 分钟
MAX_CITATION_CHARS = 60
# 只有短的、成对的中文引号内容才算「直接引用」，不参与查重：
# 不跨行、不超过 60 字，避免用引号把大段照搬的内容包起来逃过检查。英文双引号无法判断配对，一律照常检查。
CITATION = re.compile(
    rf"“[^“”\n]{{1,{MAX_CITATION_CHARS}}}”|「[^「」\n]{{1,{MAX_CITATION_CHARS}}}」|『[^『』\n]{{1,{MAX_CITATION_CHARS}}}』"
)
NON_WORD = re.compile(r"[\W_]+")


@dataclass(frozen=True)
class Overlap:
    bvid: str  # 重合片段第一个 n-gram 所在的视频
    text: str


def strip_front_matter(text: str) -> str:
    """去掉开头 --- 到下一行单独的 --- 之间的 YAML front matter；没有结束标记时原样返回。"""
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        return text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[index + 1:]).lstrip("\n")
    return text


def _normalise(text: str) -> str:
    return NON_WORD.sub("", text)


def _index(corpus: dict[str, str], n: int) -> dict[str, str]:
    grams: dict[str, str] = {}
    for bvid, text in corpus.items():
        norm = _normalise(text)
        for i in range(len(norm) - n + 1):
            grams.setdefault(norm[i:i + n], bvid)
    return grams


def _runs(positions: list[int]) -> list[tuple[int, int]]:
    """把有序的位置列表合并成连续区间 [(起点, 终点)]，例如 [1,2,3,7,8] -> [(1,3),(7,8)]。"""
    runs: list[tuple[int, int]] = []
    for pos in positions:
        if runs and pos == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], pos)
        else:
            runs.append((pos, pos))
    return runs


def _spans(chunk: str, grams: dict[str, str], n: int) -> list[Overlap]:
    hits = [i for i in range(len(chunk) - n + 1) if chunk[i:i + n] in grams]
    return [Overlap(grams[chunk[a:a + n]], chunk[a:b + n]) for a, b in _runs(hits)]


def find_overlaps(draft: str, corpus: dict[str, str], min_chars: int = DEFAULT_MIN_CHARS) -> list[Overlap]:
    """返回草稿中与任一文案连续重合至少 min_chars 个字的片段（不计标点、空白和短引文）。"""
    if min_chars < 1:
        raise ValueError("min_chars 必须是正整数")
    grams = _index(corpus, min_chars)
    chunks = (_normalise(part) for part in CITATION.split(strip_front_matter(draft)))
    return [span for chunk in chunks for span in _spans(chunk, grams, min_chars)]


def draft_length(draft: str) -> tuple[int, float]:
    """口播正文的字数（不含 front matter、「> 画面」提示行和标点）及按语速估算的秒数。"""
    spoken = "\n".join(line for line in strip_front_matter(draft).splitlines() if not line.lstrip().startswith(">"))
    chars = len(_normalise(spoken))
    return chars, chars / CHARS_PER_SECOND
