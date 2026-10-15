"""分层镜头的配方：shots.json 里镜头的可选 motion 字段。

没有 motion 的镜头照旧用整张图摆动。有 motion 的镜头按配方拆成几层分别出图，
再各按自己的规则动（compose.py）。配方是封闭的几种，每种的图层和参数都是固定的。
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import LAYER_STYLE

GREEN = (0, 255, 0)
MAGENTA = (255, 0, 255)
_FILL = {GREEN: "完全均匀的纯绿色（#00FF00）", MAGENTA: "完全均匀的纯品红色（#FF00FF）"}

# 图层的角色决定提示词怎么补：
#   backdrop  整幅背景，用视频的 style，不画人物
#   window    整幅背景，透出下一层的区域填绿色
#   scenery   横向风景条，会在窗后循环平移
#   cutout    单独的人物或物件，画在色键底上，合成时抠出
BACKDROP, WINDOW, SCENERY, CUTOUT = "backdrop", "window", "scenery", "cutout"
_NO_PEOPLE = "这一层不要画人物。"
_WINDOW_HOLE = "需要透出窗外景色的区域（窗玻璃）用{fill}平涂，画面其他地方不要出现绿色。"
_SCENERY = "这是一条横向铺满整个画面的风景，不要画人物，不要画窗框或边框，左右两端的地平线高度一致。"
_CUTOUT = "只画上面写的东西，不画地面，不画影子。除此之外整个背景是{fill}平涂，没有任何其他物体。"

MIN_SPEED, MAX_SPEED = 100, 3000
MAX_LAYER_TEXT = 500
MIN_GLOW_CHROMA = 0.08


REQUIRED = object()


@dataclass(frozen=True)
class Param:
    name: str
    valid: Callable[[Any], bool]
    hint: str
    default: Any = REQUIRED  # 写成 null 或不写都取默认值；没有默认值的必填


@dataclass(frozen=True)
class Recipe:
    layers: tuple[tuple[str, str, tuple[int, int, int] | None], ...]  # （名字，角色，色键颜色）
    params: tuple[Param, ...]


def _number(value: Any) -> bool:
    return type(value) in (int, float)


def _fraction(value: Any) -> bool:
    return _number(value) and 0.05 <= value <= 1.0


def _point(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) == 2 and all(_number(v) and 0 <= v <= 1 for v in value)


def _speed(value: Any) -> bool:
    return _number(value) and MIN_SPEED <= value <= MAX_SPEED


def chroma_distance(colour: str) -> float:
    """颜色到中性色（白、灰、黑）的色度距离，和 ffmpeg chromakey 的算法一致（BT.601 的 U/V，0–1）。"""
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    u = ((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128
    v = ((112 * r - 94 * g - 18 * b + 128) >> 8) + 128
    return math.hypot(u - 128, v - 128) / math.hypot(255, 255)


def _colour(value: Any) -> bool:
    # 太淡的颜色和白色、灰色分不开：glow 会把白头、灰色物体一起点亮。
    return (isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value) is not None
            and chroma_distance(value) >= MIN_GLOW_CHROMA)


RECIPES: dict[str, Recipe] = {
    # 坐车、看窗外：车厢不动，窗外风景循环掠过，人物随车颠簸。
    "scroll": Recipe((("bg", WINDOW, GREEN), ("scenery", SCENERY, None), ("fg", CUTOUT, MAGENTA)),
                     (Param("speed", _speed, f"每秒移动的像素，{MIN_SPEED}–{MAX_SPEED}", 700),)),
    # 象征物变大或变小（圈子、距离）：道具以人物脚下为中心从 from 缓到 to（画面宽度的比例），人物轻微呼吸。
    # 人物统一缩到画面高度的三分之一，脚踩在 feet（画面比例 [x, y]，默认是背景地平线附近）。
    "scale": Recipe((("bg", BACKDROP, None), ("prop", CUTOUT, GREEN), ("fg", CUTOUT, MAGENTA)),
                    (Param("from", _fraction, "起始宽度占画面宽度的比例，0.05–1"),
                     Param("to", _fraction, "结束宽度占画面宽度的比例，0.05–1"),
                     Param("feet", _point, "人物脚的位置 [x, y]，都是 0–1 的画面比例", (0.5, 0.79)))),
    # 蒸汽、烟：两缕交替从 at（画面坐标比例 [x, y]）升起、淡出。
    "rise": Recipe((("bg", BACKDROP, None), ("puff", CUTOUT, MAGENTA)),
                   (Param("at", _point, "升起的起点 [x, y]，都是 0–1 的画面比例"),)),
    # 整张图里某种颜色的区域（如两个圈的重叠处）缓慢明暗呼吸，不需要额外出图。
    "glow": Recipe((), (Param("color", _colour, "要发光的颜色，#RRGGBB，不能太接近白色或灰色"),)),
}


@dataclass(frozen=True)
class Layer:
    name: str
    text: str


@dataclass(frozen=True)
class Motion:
    kind: str
    layers: tuple[Layer, ...]
    params: tuple[tuple[str, Any], ...]

    def param(self, name: str) -> Any:
        return dict(self.params)[name]

    def key(self, layer: str) -> tuple[int, int, int] | None:
        return next(k for n, _, k in RECIPES[self.kind].layers if n == layer)

    def role(self, layer: str) -> str:
        return next(r for n, r, _ in RECIPES[self.kind].layers if n == layer)


def _layer_errors(raw: Any, recipe: Recipe, label: str) -> list[str]:
    expected = [n for n, _, _ in recipe.layers]
    if not expected:
        return [f"{label}：这种 motion 不需要 layers"] if raw is not None else []
    if not isinstance(raw, dict):
        return [f"{label}：layers 应该是对象，要有 {', '.join(expected)}"]
    errors = [f"{label}：缺少图层 {n}" for n in expected if n not in raw]
    errors += [f"{label}：不认识的图层 {n}，应该是 {', '.join(expected)}" for n in raw if n not in expected]
    errors += [f"{label}：图层 {n} 的描述不能为空，也不能超过 {MAX_LAYER_TEXT} 个字" for n in expected
               if n in raw and (not isinstance(raw[n], str) or not raw[n].strip() or len(raw[n]) > MAX_LAYER_TEXT)]
    return errors


def _param_errors(raw: dict[str, Any], recipe: Recipe, label: str) -> list[str]:
    known = {p.name for p in recipe.params} | {"type", "layers"}
    errors = [f"{label}：motion 里不认识的字段 {k}" for k in raw if k not in known]
    for p in recipe.params:
        value = _value(raw, p)
        if value is REQUIRED or not p.valid(value):
            errors.append(f"{label}：{p.name} 不对（{p.hint}）")
    if not errors and raw.get("from") is not None and raw.get("from") == raw.get("to"):
        errors.append(f"{label}：from 和 to 一样，就没有变化了")
    return errors


def _value(raw: dict[str, Any], param: Param) -> Any:
    """参数值；JSON 列表转成元组，存进 Motion 以后就改不动了。"""
    value = raw.get(param.name)
    value = param.default if value is None else value
    return tuple(value) if isinstance(value, list) else value


def parse_motion(raw: Any, label: str) -> tuple[Motion | None, list[str]]:
    """校验镜头的 motion 字段；没有这个字段时返回 (None, [])，即整张图摆动。"""
    if raw is None:
        return None, []
    if not isinstance(raw, dict):
        return None, [f"{label}：motion 应该是对象"]
    kind = raw.get("type")
    recipe = RECIPES.get(kind) if isinstance(kind, str) else None
    if recipe is None:
        return None, [f"{label}：motion 的 type 应该是 {' / '.join(RECIPES)} 之一"]
    errors = _layer_errors(raw.get("layers"), recipe, label) + _param_errors(raw, recipe, label)
    if errors:
        return None, errors
    layers = tuple(Layer(n, raw["layers"][n].strip()) for n, _, _ in recipe.layers)
    params = tuple((p.name, _value(raw, p)) for p in recipe.params)
    return Motion(raw["type"], layers, params), []


def layer_prompt(style: str, bg: str, motion: Motion, layer: Layer) -> str:
    """单个图层的出图提示词：画风 + 镜头里写的描述 + 按角色补上的约束（色键底色、不画人物等）。"""
    role, key = motion.role(layer.name), motion.key(layer.name)
    if role == BACKDROP:
        return style.format(bg=bg) + layer.text + "。" + _NO_PEOPLE
    if role == WINDOW:
        return style.format(bg=bg) + layer.text + "。" + _NO_PEOPLE + _WINDOW_HOLE.format(fill=_FILL[GREEN])
    if role == SCENERY:
        return LAYER_STYLE + layer.text + "。" + _SCENERY
    return LAYER_STYLE + layer.text + "。" + _CUTOUT.format(fill=_FILL[key])


def max_layered(n_shots: int) -> int:
    """一条视频里分层镜头最多占三分之一：只给真正需要「环境自己动」的镜头用。"""
    return n_shots // 3
