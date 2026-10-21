"""视频平台的封面：一个镜头的整幅图 + 倾斜的大字。

图都是纯色平涂的背景，所以先取出背景主色和人物所在的范围，在同色画布上把人物放大摆到一侧，
另一侧整块留给文字，字就不会压到人物上。配色从背景色推出来：白字 + 互补色的强调行，
深色描边和同色系的立体厚度。只用 ffmpeg：像素缩小成 ppm 在 Python 里数（同 compose.measure），
字用 drawtext 画在透明画布上，整体用 rotate 转一个角度。
"""

import colorsys
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .compose import LOOK, Box, parse_ppm
from .config import FRAME_H, FRAME_W
from .errors import VideoError
from .ffmpeg import Runner, ffmpeg, filter_escape

RGB = tuple[int, int, int]

PROBE_W, PROBE_H = 192, 108
BG_TOLERANCE = 60  # 和背景色的 RGB 距离（三通道差之和）超过它才算画了东西
MIN_HITS = 2  # 一行或一列至少这么多个点才算，孤立的噪点不算
SUBJECT_PAD = 0.03  # 人物范围四周多留画面宽、高的这个比例

MAX_LINES = 3
MAX_LINE_EM = 9.0  # 一行最宽几个字号：中文约 11 个字，英文约 20 个字母
CJK_EM, LATIN_EM = 0.8, 0.45  # 得意黑的字宽（实测汉字 0.80、英文平均 0.43 个字号）
NORMAL_SCALE = 0.82  # 普通行比强调行小一些
LINE_GAP = 1.08
MAX_FONT = 330
STAGGER = 0.15  # 每行左右错开几个字号，不排成一条竖线
DEPTH_STEPS = 18  # 立体厚度是字号的几分之一
STROKE_STEPS = 22  # 描边粗细是字号的几分之一
GREY_SATURATION = 0.15  # 背景饱和度低于它就算灰，互补色没有意义，强调色改用暖黄
WARM_YELLOW = "0xFFD23F"
WHITE = "0xFFFFFF"
_EMPHASIS = re.compile(r"\*\*(.+)\*\*")


@dataclass(frozen=True)
class Layout:
    name: str
    width: int
    height: int
    subject: Box  # 人物放进这个框（保持比例、居中）
    text: Box  # 字排在这个框里，再整体旋转
    angle: float  # 文字旋转角度（度，负数是向右上倾斜）


LAYOUTS = {
    "16x9": Layout("16x9", 1920, 1080, Box(1000, 65, 880, 972), Box(30, 60, 1010, 960), -7),
    "3x4": Layout("3x4", 1080, 1440, Box(54, 576, 972, 806), Box(54, 58, 972, 518), -6),
}


@dataclass(frozen=True)
class Palette:
    background: str
    fill: str
    accent: str
    depth: str
    stroke: str


@dataclass(frozen=True)
class Line:
    text: str
    emphasis: bool


def _hex(rgb: tuple[float, float, float]) -> str:
    return "0x" + "".join(f"{round(c * 255):02X}" for c in rgb)


def dominant_color(pixels: bytes) -> RGB:
    """出现最多的颜色：按每通道 16 级分档数，再取这一档里像素的平均值。"""
    if not pixels:
        raise VideoError("封面探测图是空的")
    triples = [(pixels[i], pixels[i + 1], pixels[i + 2]) for i in range(0, len(pixels) - 2, 3)]
    band = Counter((r >> 4, g >> 4, b >> 4) for r, g, b in triples).most_common(1)[0][0]
    members = [p for p in triples if (p[0] >> 4, p[1] >> 4, p[2] >> 4) == band]
    r, g, b = (round(sum(c) / len(members)) for c in zip(*members))
    return r, g, b


def subject_box(width: int, height: int, pixels: bytes, bg: RGB) -> Box | None:
    """不是背景色的部分（人物和道具）的范围，换算到 1920x1080，四周留一点边；整张都是背景色时返回 None。"""
    def far(i: int) -> bool:
        r, g, b = pixels[3 * i:3 * i + 3]
        return abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2]) > BG_TOLERANCE

    hits = [[far(y * width + x) for x in range(width)] for y in range(height)]
    cols = [x for x in range(width) if sum(row[x] for row in hits) >= MIN_HITS]
    rows = [y for y in range(height) if sum(hits[y]) >= MIN_HITS]
    if not cols or not rows:
        return None
    kx, ky = FRAME_W / width, FRAME_H / height
    pad_x, pad_y = int(FRAME_W * SUBJECT_PAD), int(FRAME_H * SUBJECT_PAD)
    x0, y0 = max(0, int(cols[0] * kx) - pad_x), max(0, int(rows[0] * ky) - pad_y)
    x1, y1 = min(FRAME_W, int((cols[-1] + 1) * kx) + pad_x), min(FRAME_H, int((rows[-1] + 1) * ky) + pad_y)
    return Box(x0, y0, x1 - x0, y1 - y0)


def palette(bg: RGB) -> Palette:
    hue, sat, val = colorsys.rgb_to_hsv(*(c / 255 for c in bg))
    accent = _hex(colorsys.hsv_to_rgb((hue + 0.5) % 1, 0.85, 1.0)) if sat > GREY_SATURATION else WARM_YELLOW
    return Palette(
        background="0x" + "".join(f"{c:02X}" for c in bg),
        fill=WHITE,
        accent=accent,
        depth=_hex(colorsys.hsv_to_rgb(hue, min(1.0, sat + 0.2), val * 0.35)),
        stroke=_hex(colorsys.hsv_to_rgb(hue, min(1.0, sat + 0.3), 0.10)),
    )


def _char_em(ch: str) -> float:
    if unicodedata.combining(ch) or unicodedata.category(ch) == "Cf":
        return 0.0  # 组合符号、零宽字符不占宽度
    wide = unicodedata.east_asian_width(ch) in ("W", "F")
    # “”‘’…— 这类宽度不定的标点，在中文字体里按全角画
    ambiguous_punct = unicodedata.east_asian_width(ch) == "A" and unicodedata.category(ch).startswith("P")
    return CJK_EM if wide or ambiguous_punct else LATIN_EM


def text_em(text: str) -> float:
    """一行字大约多宽（以字号为单位）：全角字按汉字算，其余按英文算。"""
    return sum(_char_em(ch) for ch in text)


def parse_text(text: str) -> tuple[Line, ...]:
    """封面字：一行一句，最多三行；**整行** 是强调行，用强调色、字更大。"""
    if not text.strip():
        raise VideoError("封面字不能为空")
    rows = text.strip().split("\n")
    if len(rows) > MAX_LINES:
        raise VideoError(f"封面字最多 {MAX_LINES} 行，现在 {len(rows)} 行")
    lines = []
    for row in (r.strip() for r in rows):
        match = _EMPHASIS.fullmatch(row)
        body = match[1].strip() if match else row
        if not body or "**" in body or not text_em(body):
            raise VideoError(f"封面字「{row}」不对：不能有空行，强调要用 ** 包住整行")
        if text_em(body) > MAX_LINE_EM:
            raise VideoError(f"封面字「{body}」太长，缩短或分成两行")
        lines.append(Line(body, match is not None))
    return tuple(lines)


def font_sizes(lines: tuple[Line, ...], box: Box) -> tuple[int, ...]:
    """每行撑满文字框的宽度（强调行最大），总高度放不下就整体缩小。

    宽度要给左右错开、立体厚度和两侧描边留出余量，否则画布边缘会把字裁掉。
    """
    margin = STAGGER * (len(lines) - 1) + 1 / DEPTH_STEPS + 2 / STROKE_STEPS
    wide = [box.w * (1.0 if line.emphasis else NORMAL_SCALE) / (text_em(line.text) + margin) for line in lines]
    shrink = min(1.0, box.h / (sum(wide) * LINE_GAP))
    return tuple(int(min(size * shrink, MAX_FONT)) for size in wide)


def _text_chain(lines: tuple[Line, ...], files: tuple[Path, ...], font: Path, colours: Palette,
                layout: Layout) -> str:
    """透明画布上逐行画字：同色系深色的立体厚度（向右下叠几层）→ 描边的正文，最后整体旋转。"""
    box, sizes = layout.text, font_sizes(lines, layout.text)
    chain = [f"color=c=black@0:s={box.w}x{box.h}", "format=rgba"]
    y = (box.h - int(sum(sizes) * LINE_GAP)) // 2
    for i, (line, file, size) in enumerate(zip(lines, files, sizes)):
        x = f"(w-text_w)/2+{round((i - (len(lines) - 1) / 2) * size * STAGGER)}"
        # expansion=none：不展开 %{...}，封面字里的 % 和反斜杠原样画出来
        common = (f"drawtext=fontfile={filter_escape(font)}:textfile={filter_escape(file)}:fontsize={size}"
                  ":expansion=none")
        border = max(2, size // STROKE_STEPS)
        for d in range(max(4, size // DEPTH_STEPS), 0, -2):
            chain.append(f"{common}:fontcolor={colours.depth}:borderw={border}:bordercolor={colours.depth}"
                         f":x={x}+{d}:y={y + d}")
        chain.append(f"{common}:fontcolor={colours.accent if line.emphasis else colours.fill}"
                     f":borderw={border}:bordercolor={colours.stroke}:x={x}:y={y}")
        y += int(size * LINE_GAP)
    angle = f"{layout.angle}*PI/180"
    chain.append(f"rotate={angle}:c=none:ow=rotw({angle}):oh=roth({angle})")
    return ",".join(chain)


def _fit(subject: Box, area: Box) -> Box:
    """人物按比例缩放进区域，居中；宽高取偶数。"""
    scale = min(area.w / subject.w, area.h / subject.h)
    w, h = int(subject.w * scale) // 2 * 2, int(subject.h * scale) // 2 * 2
    return Box(area.x + (area.w - w) // 2, area.y + (area.h - h) // 2, w, h)


def build_graph(layout: Layout, subject: Box, colours: Palette, text_chain: str) -> str:
    placed, text = _fit(subject, layout.subject), layout.text
    return (f"color=c={colours.background}:s={layout.width}x{layout.height}[canvas];"
            f"[0:v]scale={FRAME_W}:{FRAME_H},crop={subject.w}:{subject.h}:{subject.x}:{subject.y},"
            f"scale={placed.w}:{placed.h}[subj];"
            f"[canvas][subj]overlay={placed.x}:{placed.y}:shortest=1[base];"
            f"{text_chain}[txt];"
            f"[base][txt]overlay=x={text.x + text.w // 2}-w/2:y={text.y + text.h // 2}-h/2,"
            f"{LOOK},noise=alls=6:allf=u,format=yuv420p")


def make_thumbnail(image: Path, out: Path, lines: tuple[Line, ...], layout: Layout, font: Path, tmp: Path,
                   runner: Runner) -> None:
    tmp.mkdir(parents=True, exist_ok=True)
    probe = tmp / f"{out.stem}.ppm"
    ffmpeg(["-i", image, "-vf", f"scale={PROBE_W}:{PROBE_H},format=rgb24", "-frames:v", "1",
            "-f", "image2", "-c:v", "ppm", probe], runner)
    try:
        width, height, pixels = parse_ppm(probe.read_bytes())
    finally:
        probe.unlink(missing_ok=True)
    bg = dominant_color(pixels)
    subject = subject_box(width, height, pixels, bg) or Box(0, 0, FRAME_W, FRAME_H)
    files = tuple(tmp / f"{out.stem}.line{i}.txt" for i in range(len(lines)))
    for file, line in zip(files, lines):
        file.write_text(line.text, encoding="utf-8")  # 用文件传字，免得在滤镜参数里转义
    colours = palette(bg)
    graph = build_graph(layout, subject, colours, _text_chain(lines, files, font, colours, layout))
    ffmpeg(["-i", image, "-filter_complex", graph, "-frames:v", "1", "-q:v", "2", out], runner)
