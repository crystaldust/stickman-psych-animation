"""分层镜头的合成：每种配方一张 ffmpeg 滤镜图，输出 [comp]，之后接和普通镜头一样的质感、颗粒、字幕。

位置都从图层实际画出来的范围算（抠掉色键底色后的包围盒），不写死像素坐标；
包围盒用 ffmpeg 缩小成一张 ppm 再在 Python 里数像素，不需要额外的依赖。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .config import EDGE_BLUR, EDGE_ROW, FPS, FRAME_H, FRAME_W, VERT_CROP_W
from .errors import VideoError
from .ffmpeg import Runner, ffmpeg
from .motion import GREEN, MAGENTA, Motion, chroma_distance

FIT = f"scale=1952:1098,crop={FRAME_W}:{FRAME_H}"
LOOK = "rgbashift=rh=-3:bh=3,gblur=sigma=0.6,eq=saturation=1.05,vignette=PI/6"
KEY_FILTERS = {GREEN: "chromakey=0x00FF00:0.18:0.04", MAGENTA: "chromakey=0xFF00FF:0.22:0.05"}

PROBE_W, PROBE_H = 480, 270
KEY_TOLERANCE = 90  # 和色键颜色的 RGB 距离（三通道差之和）超过它才算画了东西
MAX_ASPECT = 8  # 图层内容的长宽比超过它就是没画对（一条细线、几个散落的噪点），缩放后尺寸会失控
_PPM_HEADER = re.compile(rb"P6\s+(\d+)\s+(\d+)\s+255\s")

BOB_PX, BOB_PERIOD = 4, 0.55  # scroll：随车颠簸
BREATH = "2*sin(2*PI*t/3)"  # scale：人物缓慢呼吸起伏（像素）
CHAR_H = FRAME_H // 3  # scale：人物统一的高度
PUFF_H, PUFF_RISE, PUFF_PERIOD, PUFF_SWAY = 130, 45, 2.4, 6  # rise：一缕蒸汽的高度、上升距离、周期、左右飘
GLOW_TINT, GLOW_PERIOD, GLOW_MAX_TOLERANCE = "0xFFF4C0", 2.6, 0.16


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class Graph:
    inputs: tuple[Path, ...]
    graph: str  # 以 [comp] 结尾


Probe = Callable[[str, tuple[int, int, int]], Box]


def parse_ppm(data: bytes) -> tuple[int, int, bytes]:
    # 头部之后只有一个空白字符；像素字节可能正好是空白的值，不能用 split 去切。
    header = _PPM_HEADER.match(data)
    if header is None:
        raise VideoError("读不出图层探测图（不是 8 位 P6 ppm）")
    width, height = int(header[1]), int(header[2])
    pixels = data[header.end():]
    if len(pixels) != width * height * 3:
        raise VideoError(f"图层探测图大小不对：{width}x{height} 应有 {width * height * 3} 字节，实际 {len(pixels)}")
    return width, height, pixels


def content_box(width: int, height: int, pixels: bytes, key: tuple[int, int, int],
                tolerance: int = KEY_TOLERANCE) -> Box | None:
    """不是色键颜色的像素的包围盒，换算到 1920x1080 画面坐标；整张都是色键颜色时返回 None。"""
    xs, ys = [], []
    for i in range(width * height):
        r, g, b = pixels[3 * i:3 * i + 3]
        if abs(r - key[0]) + abs(g - key[1]) + abs(b - key[2]) > tolerance:
            xs.append(i % width)
            ys.append(i // width)
    if not xs:
        return None
    kx, ky = FRAME_W / width, FRAME_H / height
    x0, y0 = min(xs), min(ys)
    return Box(int(x0 * kx), int(y0 * ky), int((max(xs) + 1 - x0) * kx), int((max(ys) + 1 - y0) * ky))


def measure(image: Path, key: tuple[int, int, int], probe: Path, runner: Runner) -> Box:
    ffmpeg(["-f", "image2", "-i", image, "-vf", f"{FIT},scale={PROBE_W}:{PROBE_H},format=rgb24",
            "-frames:v", "1", "-f", "image2", "-c:v", "ppm", probe], runner)
    try:
        box = content_box(*parse_ppm(probe.read_bytes()), key)
    finally:
        probe.unlink(missing_ok=True)
    if box is None:
        raise VideoError(f"{image} 抠掉底色后什么都没剩，重新生成这一层")
    if max(box.w, box.h) > MAX_ASPECT * max(1, min(box.w, box.h)):
        raise VideoError(f"{image} 抠出来的内容是 {box.w}x{box.h} 的细条，不像一个完整的东西，重新生成这一层")
    return box


def portrait_graph(src: str, out: str, width: int, height: int, top: int) -> str:
    """1920x1080 画面排进竖幅：裁中间 4:3 缩到 width 宽，上边缘放在 top；上下各用靠边的一行像素拉满再模糊，
    看起来像同一张图延伸出去。竖屏视频和竖版封面共用。"""
    pic_h = width * FRAME_H // VERT_CROP_W // 2 * 2
    bottom = height - top - pic_h
    return (f"[{src}]crop={VERT_CROP_W}:{FRAME_H}:{(FRAME_W - VERT_CROP_W) // 2}:0,scale={width}:{pic_h},"
            "format=yuv444p,split=3[vpic][vtop][vbot];"  # yuv420 下单行像素的色度会错位，延伸区发黑
            f"[vtop]crop={width}:1:0:{EDGE_ROW},scale={width}:{top},gblur=sigma={EDGE_BLUR}[vtop2];"
            f"[vbot]crop={width}:1:0:{pic_h - 1 - EDGE_ROW},scale={width}:{bottom},gblur=sigma={EDGE_BLUR}[vbot2];"
            # 一行拉成几百行时 scale 会改 SAR，vignette 按 SAR 算暗角会把整帧压黑，所以拼好后归一
            f"[vtop2][vpic][vbot2]vstack=3,setsar=1[{out}]")


def _keyed(index: int, key: tuple[int, int, int], crop: Box | None = None) -> str:
    cut = f",crop={crop.w}:{crop.h}:{crop.x}:{crop.y}" if crop else ""
    return f"[{index}:v]{FIT}{cut},format=yuva444p,{KEY_FILTERS[key]}"


def _scroll(motion: Motion) -> str:
    # 风景条 = 原图 + 镜像 + 原图，平移一个 2 倍宽度的周期正好接上，循环看不出接缝。
    speed = motion.param("speed")
    return ";".join([
        f"[1:v]{FIT},format=yuv444p,split=3[a][b][c];[b]hflip[bf];[a][bf][c]hstack=3,"
        f"crop={FRAME_W}:{FRAME_H}:x='mod(t*{speed:g},{2 * FRAME_W})':y=0[scen]",
        _keyed(0, GREEN) + "[bg]",
        _keyed(2, MAGENTA) + "[fg]",
        "[scen][bg]overlay=0:0[room]",
        f"[room][fg]overlay=x=0:y='-{BOB_PX}*abs(sin(PI*t/{BOB_PERIOD}))'[comp]",
    ])


def _ease(start: float, end: float, dur: float) -> str:
    """整段时长内从 start 平滑过渡到 end（smoothstep）。"""
    p = f"min(1,t/{dur:.3f})"
    return f"({start:g}+({end:g}-({start:g}))*{p}*{p}*(3-2*{p}))"


def _to_frame(point: tuple[float, float]) -> tuple[int, int]:
    return round(point[0] * FRAME_W), round(point[1] * FRAME_H)


def _scale(motion: Motion, dur: float, probe: Probe) -> str:
    # 生成出来的人物大小不定：裁出来统一缩到 CHAR_H 高，脚踩在 feet 上；道具以脚为中心缩放。
    prop, fg = probe("prop", GREEN), probe("fg", MAGENTA)
    fg_w = max(2, round(fg.w * CHAR_H / fg.h / 2) * 2)
    feet_x, feet_y = _to_frame(motion.param("feet"))
    width = _ease(round(motion.param("from") * FRAME_W), round(motion.param("to") * FRAME_W), dur)
    return ";".join([
        f"[0:v]{FIT}[bg]",
        _keyed(1, GREEN, prop) + f",scale=w='trunc({width}/2)*2':h=-2:eval=frame[prop]",
        _keyed(2, MAGENTA, fg) + f",scale={fg_w}:{CHAR_H}[fg]",
        f"[bg][prop]overlay=x='{feet_x}-w/2':y='{feet_y}-h/2'[b1]",
        f"[b1][fg]overlay=x={feet_x - fg_w // 2}:y='{feet_y - CHAR_H}+{BREATH}'[comp]",
    ])


def _rise(motion: Motion, probe: Probe) -> str:
    puff = probe("puff", MAGENTA)
    width = max(2, round(puff.w * PUFF_H / puff.h / 2) * 2)
    at_x, at_y = _to_frame(motion.param("at"))
    chains = [f"[0:v]{FIT}[bg]", _keyed(1, MAGENTA, puff) + f",scale={width}:{PUFF_H}[st]", "[st]split[s0][s1]"]
    below = "bg"
    for i, phase in enumerate((0.0, 0.5)):
        progress = f"mod(t/{PUFF_PERIOD}+{phase},1)"
        chains.append(f"[s{i}]geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)'"
                      f":a='alpha(X,Y)*sin(PI*mod(T/{PUFF_PERIOD}+{phase},1))'[p{i}]")
        out = "comp" if i == 1 else f"o{i}"
        chains.append(f"[{below}][p{i}]overlay=x='{at_x}-{width // 2}+{PUFF_SWAY}*sin(2*PI*t/1.7+{i * 2})'"
                      f":y='{at_y}-{PUFF_H}-{PUFF_RISE}*{progress}'[{out}]")
        below = out
    return ";".join(chains)


def _glow(motion: Motion) -> str:
    # 容差不超过颜色到中性色距离的一半：白头、灰色物体永远落在容差外，不会跟着亮。
    tolerance = min(GLOW_MAX_TOLERANCE, round(chroma_distance(motion.param("color")) / 2, 3))
    colour = "0x" + motion.param("color")[1:].upper()
    return ";".join([
        f"[0:v]{FIT},split[bg][m0]",
        f"[m0]scale={PROBE_W}:{PROBE_H},format=yuva444p,chromakey={colour}:{tolerance:g}:0.02,alphaextract,negate,"
        "gblur=sigma=6[mask]",
        f"color=c={GLOW_TINT}:s={PROBE_W}x{PROBE_H}:r={FPS},format=yuva444p[col]",
        "[col][mask]alphamerge,geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)'"
        f":a='alpha(X,Y)*(0.15+0.45*(0.5+0.5*sin(2*PI*T/{GLOW_PERIOD})))',scale={FRAME_W}:{FRAME_H}[glow]",
        "[bg][glow]overlay=0:0:shortest=1[comp]",
    ])


def build_graph(motion: Motion, layers: dict[str, Path], image: Path, dur: float, probe: Probe) -> Graph:
    """输入按配方的图层顺序排（glow 只用整张图）；probe(图层名, 色键颜色) 返回该层内容的包围盒。"""
    graphs: dict[str, Callable[[], str]] = {
        "scroll": lambda: _scroll(motion),
        "scale": lambda: _scale(motion, dur, probe),
        "rise": lambda: _rise(motion, probe),
        "glow": lambda: _glow(motion),
    }
    if motion.kind not in graphs:
        raise VideoError(f"不认识的分层配方：{motion.kind}")
    inputs = (image,) if not motion.layers else tuple(layers[layer.name] for layer in motion.layers)
    return Graph(inputs, graphs[motion.kind]())
