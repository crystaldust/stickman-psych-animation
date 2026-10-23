"""旁白文本的几种形态：送去配音的、带时间戳的、上字幕的。"""

import logging
import re

import jieba
import jieba.posseg as pseg

from .config import MAX_SUB_CHARS, MAX_SUB_CHARS_EN

jieba.setLogLevel(logging.WARNING)  # 不打印加载词典的进度

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
# 长字幕切在哪个词后面：jieba 词性里，介词、连词、副词、代词粘着后面的词，助词、语气词、方位词粘着前面的词。
# 按前缀匹配（p 也匹配 pba、pbei）；zg 是 jieba 给「很」这类字标的词性。
_BINDS_NEXT = ("p", "c", "d", "r", "zg")
_BINDS_PREV = ("u", "y", "f")
# 切点别扭时，折算成「离均分点多出几个字」的罚分；切在引号里面罚得更重。
_AWKWARD_CUT = 3
_INSIDE_QUOTE_CUT = 6
_SUB_QUOTED = re.compile(r"「[^「」]*」|“[^“”]*”|《[^《》]*》")
_SPOKEN_CHAR = re.compile(r"[一-鿿0-9A-Za-z]")
_EN_SPOKEN = re.compile(r"[a-z0-9]")
# 断句：句末标点（或分号、冒号）后面跟着大写开头的词才断，「wait... and then」这类不断。
# 缩写（Mr. Smith）分不出来，所以译文里不要用缩写。
_EN_SENTENCE = re.compile(r"(?<=[.!?…;:])\s+(?=['‘(]?[A-Z])")
_EN_CLAUSE = re.compile(r"(?<=,)\s+")
# 英文长字幕不切在这些词后面：冠词、介词、物主代词、连词都粘着后面的词。
# 后面紧跟连词时不算（「can get in | and you…」本来就是两个分句）。
BINDS_NEXT_EN = frozenset(
    "a an the to of in on at for with from by into onto about than as "
    "and or but if that because my your his her its our their".split())
_CLAUSE_START_EN = frozenset("and or but so because if when while".split())
_AWKWARD_CUT_EN = 12  # 切点别扭时，折算成「离均分点多出几个字符」的罚分


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


def _awkward(word, nxt) -> bool:
    """在 word 和 nxt 之间切开读着别扭：一边粘着另一边，或挨着单字（词典里没有的人名会被拆成单字）。"""
    if word.flag.startswith(_BINDS_NEXT) or nxt.flag.startswith(_BINDS_PREV):
        return True
    lone = (len(word.word) == 1 or len(nxt.word) == 1) and "x" not in (word.flag, nxt.flag)
    return lone and not word.flag.startswith(_BINDS_PREV) and not nxt.flag.startswith(_BINDS_NEXT)


def _cut_costs(text: str) -> dict[int, int]:
    """能下刀的位置 → 罚分。只切在词和词之间，不拆书名，不让引号落单。"""
    atom_ends = {m.end() for m in _SUB_ATOM.finditer(text)}
    quoted = {i for m in _SUB_QUOTED.finditer(text) for i in range(m.start() + 1, m.end())}
    words = list(pseg.cut(text, HMM=False))
    costs, pos = {}, 0
    for word, nxt in zip(words, words[1:]):
        pos += len(word.word)
        if pos in atom_ends:
            costs[pos] = _AWKWARD_CUT * _awkward(word, nxt) + _INSIDE_QUOTE_CUT * (pos in quoted)
    return costs


def _word_split(text: str) -> list[str] | None:
    """过长的一段均分成尽量少的几条，每刀切在离均分点近、又不别扭的词边界上；切不出来返回 None。"""
    costs = _cut_costs(text)
    out, start = [], 0
    for left in range(-(-len(text) // MAX_SUB_CHARS), 1, -1):
        target = start + (len(text) - start) / left
        fits = [c for c in costs if 0 < c - start <= MAX_SUB_CHARS and len(text) - c <= MAX_SUB_CHARS * (left - 1)]
        if not fits:
            return None
        cut = min(fits, key=lambda c: (abs(c - target) + costs[c], c))
        out.append(text[start:cut].strip())
        start = cut
    return out + [text[start:].strip()]


def _hard_split(text: str) -> list[str]:
    """找不到合适词边界时的退路：按字数硬切，但不拆书名、不让引号落单；书名本身超长才拆开它。"""
    out, line = [], ""
    for atom in _SUB_ATOM.findall(text):
        for piece in (atom if len(atom) > MAX_SUB_CHARS else (atom,)):
            if line and len(line) + len(piece) > MAX_SUB_CHARS:
                out.append(line)
                line = ""
            line += piece
    return out + [line] if line else out


def split_sub(text: str) -> list[str]:
    """一行旁白切成字幕：按标点断开，超过 MAX_SUB_CHARS 的在词边界上均分，去掉句末标点（后引号、后书名号留着）。"""
    out = []
    for chunk in (c for c in _SUB_BREAK.sub(lambda m: m.group() + "\n", text).split("\n") if c.strip()):
        chunk = _SUB_TRAIL.sub("", chunk.strip())
        if len(chunk) <= MAX_SUB_CHARS:
            out += [chunk] if chunk else []
        else:
            out += _word_split(chunk) or _hard_split(chunk)
    return out


# ---------- 英文：配音按词返回时间戳，这里把每个词摊到它的字母上，后面的流程就和中文按字一样。


def norm_en(text: str) -> str:
    """会拿到时间戳的字符：小写字母和数字（撇号、连字符、空格、标点都不算）。"""
    return "".join(_EN_SPOKEN.findall(text.lower()))


def spoken_en(text: str) -> str:
    """送去配音的英文：双引号换成单引号，免得提前关掉提示词里包住旁白的那对双引号。"""
    return text.replace('"', "'").replace("“", "'").replace("”", "'")


def _awkward_en(word: str, nxt: str) -> bool:
    return word.lower() in BINDS_NEXT_EN and nxt.lower().strip("'") not in _CLAUSE_START_EN


def _best_cuts(words: list[str], pieces: int, limit: int) -> tuple[int, list[str]] | None:
    """切成 pieces 行、每行不超过 limit 的切法里，长短最均匀、别扭切点最少的那一种，连同它的别扭切点数；
    放不下时返回 None。动态规划：best[j][i] 是前 i 个词切成 j 行的最优（代价, 别扭切点数, 上一个切点）。"""
    ends = [0]
    for word in words:
        ends.append(ends[-1] + len(word))
    width = lambda a, b: ends[b] - ends[a] + (b - a - 1)  # noqa: E731 - words[a:b] 用空格连起来的长度
    target, n = width(0, len(words)) / pieces, len(words)
    best: list[dict[int, tuple[float, int, int]]] = [{0: (0.0, 0, -1)}] + [{} for _ in range(pieces)]
    for j in range(1, pieces + 1):
        for i in range(j, n - (pieces - j) + 1):
            for k, (cost, awkward, _) in best[j - 1].items():
                if k >= i or width(k, i) > limit:
                    continue
                bad = int(k > 0 and _awkward_en(words[k - 1], words[k]))
                total = cost + abs(width(k, i) - target) + _AWKWARD_CUT_EN * bad
                if i not in best[j] or total < best[j][i][0]:
                    best[j][i] = (total, awkward + bad, k)
    if n not in best[pieces]:
        return None
    lines, i = [], n
    for j in range(pieces, 0, -1):
        k = best[j][i][2]
        lines.append(" ".join(words[k:i]))
        i = k
    return best[pieces][n][1], lines[::-1]


def _wrap_words(text: str, limit: int) -> list[str]:
    """超长的一句按单词断成尽量少、长度相近的几行，不拆单词，也不把冠词、介词留在行尾。"""
    words = text.split()
    if len(text) <= limit or len(words) == 1:
        return [text]
    for pieces in range(-(-len(text) // limit), len(words) + 1):
        found = _best_cuts(words, pieces, limit)
        if found is None:
            continue
        awkward, lines = found
        # 最少行数避不开别扭切点时，多切一行能避开就多切一行
        more = _best_cuts(words, pieces + 1, limit) if awkward and pieces < len(words) else None
        return more[1] if more and more[0] == 0 else lines
    return words  # 有单词本身就超过上限：一个词一行


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
