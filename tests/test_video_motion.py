import pytest

from stickman.video.config import LAYER_STYLE
from stickman.video.motion import GREEN, MAGENTA, chroma_distance, layer_prompt, max_layered, parse_motion

SCROLL = {"type": "scroll", "layers": {"bg": "车厢内部，右边一扇大窗户", "scenery": "田野和电线杆", "fg": "两个火柴人并排坐着"}}
SCALE = {"type": "scale", "layers": {"bg": "空房间", "prop": "一个白色粉笔圆圈", "fg": "一个火柴人站着"}, "from": 0.6, "to": 0.16}
RISE = {"type": "rise", "layers": {"bg": "桌上一口火锅", "puff": "一缕白色蒸汽"}, "at": [0.47, 0.64]}
GLOW = {"type": "glow", "color": "#F9E440"}


def test_absent_motion_means_the_plain_sway():
    assert parse_motion(None, "s01") == (None, [])


@pytest.mark.parametrize("raw", [SCROLL, SCALE, RISE, GLOW])
def test_each_recipe_parses(raw):
    motion, errors = parse_motion(raw, "s01")

    assert errors == [] and motion.kind == raw["type"]
    assert [layer.name for layer in motion.layers] == list(raw.get("layers", {}))


def test_optional_params_get_defaults_and_given_ones_are_kept():
    scroll, _ = parse_motion(SCROLL, "s01")
    fast, _ = parse_motion({**SCROLL, "speed": 1200}, "s01")
    scale, _ = parse_motion(SCALE, "s01")

    assert scroll.param("speed") == 700 and fast.param("speed") == 1200
    assert (scale.param("from"), scale.param("to"), scale.param("feet")) == (0.6, 0.16, (0.5, 0.79))


@pytest.mark.parametrize(
    "raw, needle",
    [
        ("scroll", "对象"),
        ({"type": "zoom"}, "type"),
        ({"type": ["scroll"]}, "type"),
        ({**SCROLL, "layers": {**SCROLL["layers"], "fg": 5}}, "fg"),
        ({**SCALE, "from": True}, "from"),
        ({**SCROLL, "layers": {"bg": "x", "fg": "y"}}, "scenery"),
        ({**SCROLL, "layers": {**SCROLL["layers"], "sky": "z"}}, "sky"),
        ({**SCROLL, "layers": {**SCROLL["layers"], "fg": " "}}, "fg"),
        ({**SCROLL, "layers": "bg"}, "layers"),
        ({**SCROLL, "layers": {**SCROLL["layers"], "fg": "长" * 501}}, "fg"),
        ({**SCROLL, "speed": "fast"}, "speed"),
        ({**SCROLL, "speed": True}, "speed"),
        ({**SCROLL, "speed": 99999}, "speed"),
        ({**SCROLL, "bob": 3}, "bob"),
        ({**SCALE, "to": None}, "to"),
        ({k: v for k, v in SCALE.items() if k != "from"}, "from"),
        ({**SCALE, "to": 0.6}, "from"),
        ({**SCALE, "feet": [0.5, 2]}, "feet"),
        ({**RISE, "at": [0.5]}, "at"),
        ({**RISE, "at": [0.5, 1.5]}, "at"),
        ({**GLOW, "color": "yellow"}, "color"),
        ({**GLOW, "color": "#F0F0F0"}, "color"),
        ({**GLOW, "color": "#FAF8E8"}, "color"),
        ({**GLOW, "layers": {"bg": "x"}}, "layers"),
    ],
)
def test_bad_motion_is_reported_with_the_shot_named(raw, needle):
    motion, errors = parse_motion(raw, "s03")

    assert motion is None and errors
    assert all(e.startswith("s03") for e in errors) and needle in " ".join(errors)


def test_backdrop_layers_use_the_video_style_and_keep_people_out():
    motion, _ = parse_motion(SCALE, "s01")

    prompt = layer_prompt("画风，背景是{bg}。画面内容：", "灰色", motion, motion.layers[0])

    assert prompt.startswith("画风，背景是灰色。画面内容：空房间") and "不要画人物" in prompt


def test_window_backdrop_asks_for_a_green_hole():
    motion, _ = parse_motion(SCROLL, "s01")

    prompt = layer_prompt("{bg}", "米色", motion, motion.layers[0])

    assert "#00FF00" in prompt and "不要画人物" in prompt


@pytest.mark.parametrize("raw, index, colour", [(SCROLL, 2, "#FF00FF"), (SCALE, 1, "#00FF00"), (RISE, 1, "#FF00FF")])
def test_cutout_layers_are_drawn_alone_on_the_key_colour(raw, index, colour):
    motion, _ = parse_motion(raw, "s01")
    layer = motion.layers[index]

    prompt = layer_prompt("{bg}", "米色", motion, layer)

    assert prompt.startswith(LAYER_STYLE + layer.text) and colour in prompt and "米色" not in prompt


def test_scenery_strip_is_a_wide_landscape_without_people():
    motion, _ = parse_motion(SCROLL, "s01")

    prompt = layer_prompt("{bg}", "米色", motion, motion.layers[1])

    assert prompt.startswith(LAYER_STYLE + "田野和电线杆") and "不要画人物" in prompt


def test_key_colours_match_the_recipes():
    scroll, _ = parse_motion(SCROLL, "s01")
    scale, _ = parse_motion(SCALE, "s01")

    assert scroll.key("bg") == GREEN and scroll.key("fg") == MAGENTA and scroll.key("scenery") is None
    assert scale.key("prop") == GREEN and scale.key("bg") is None


@pytest.mark.parametrize("n, cap", [(1, 0), (2, 1), (3, 1), (14, 7), (17, 8)])
def test_at_most_half_of_the_shots_may_be_layered(n, cap):
    assert max_layered(n) == cap


def test_null_optional_params_fall_back_to_defaults():
    motion, errors = parse_motion({**SCROLL, "speed": None}, "s01")

    assert errors == [] and motion.param("speed") == 700


def test_points_are_stored_as_tuples_so_defaults_cannot_be_mutated():
    scale, _ = parse_motion(SCALE, "s01")
    rise, _ = parse_motion(RISE, "s01")

    assert scale.param("feet") == (0.5, 0.79) and rise.param("at") == (0.47, 0.64)
    assert parse_motion(SCALE, "s02")[0].param("feet") is not None


@pytest.mark.parametrize("colour, low, high", [("#FFFFFF", 0, 0.01), ("#F9F7A6", 0.1, 0.13), ("#F9E440", 0.2, 0.4)])
def test_chroma_distance_measures_how_far_a_colour_is_from_white_or_grey(colour, low, high):
    assert low <= chroma_distance(colour) <= high
