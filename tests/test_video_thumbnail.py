import colorsys
from pathlib import Path

import pytest
from video_fakes import FakeRunner

from stickman.video.compose import Box
from stickman.video.errors import VideoError
from stickman.video.thumbnail import (
    LAYOUTS,
    Line,
    dominant_color,
    font_sizes,
    make_thumbnail,
    palette,
    parse_text,
    subject_box,
    text_em,
)

BG, INK = (60, 60, 160), (0, 0, 0)


def _hsv(hex_colour: str) -> tuple[float, float, float]:
    value = int(hex_colour, 16)
    rgb = ((value >> 16) & 255, (value >> 8) & 255, value & 255)
    return colorsys.rgb_to_hsv(*(c / 255 for c in rgb))


def _graph(runner: FakeRunner) -> str:
    cmd = runner.ffmpeg_calls()[-1]
    return cmd[cmd.index("-filter_complex") + 1]


def test_dominant_colour_is_the_most_common_colour_band_averaged():
    pixels = bytes([60, 60, 160] * 5 + [62, 61, 161] * 3 + [0, 0, 0] * 4)

    assert dominant_color(pixels) == (61, 60, 160)


def test_subject_box_spans_everything_that_is_not_background_with_padding():
    # 10x5 的探测图，第 4–6 列、第 1–3 行画了东西；只有一个点的行列（噪点）不算
    grid = [[BG] * 10 for _ in range(5)]
    for y in (1, 2, 3):
        for x in (4, 5, 6):
            grid[y][x] = INK
    grid[0][9] = INK
    pixels = bytes(c for row in grid for p in row for c in p)

    box = subject_box(10, 5, pixels, BG)

    assert box.x < 4 * 192 and box.x + box.w > 7 * 192 and box.x + box.w < 9 * 192
    assert box.y < 1 * 216 and box.y + box.h > 4 * 216
    assert box.x >= 0 and box.y >= 0 and box.x + box.w <= 1920 and box.y + box.h <= 1080


def test_dominant_colour_of_an_empty_probe_is_an_error():
    with pytest.raises(VideoError):
        dominant_color(b"")


def test_subject_box_is_none_on_a_blank_image():
    assert subject_box(2, 1, bytes(BG * 2), BG) is None


def test_palette_contrasts_with_a_coloured_background_and_keeps_its_hue():
    colours = palette((60, 60, 160))
    bg_hue = _hsv("0x3C3CA0")[0]

    assert colours.background == "0x3C3CA0"
    assert colours.fill == "0xFFFFFF"
    assert abs(_hsv(colours.accent)[0] - (bg_hue + 0.5) % 1) < 0.02  # 强调色是互补色
    assert abs(_hsv(colours.depth)[0] - bg_hue) < 0.02  # 立体厚度用同色系的深色
    assert _hsv(colours.stroke)[2] < _hsv(colours.depth)[2] < _hsv(colours.background)[2]


def test_palette_uses_a_warm_yellow_on_a_grey_background():
    assert palette((76, 76, 78)).accent == "0xFFD23F"


def test_parse_text_marks_emphasised_lines():
    assert parse_text("越怕失去\n**越推开**") == (Line("越怕失去", False), Line("越推开", True))
    assert parse_text("  one line  ") == (Line("one line", False),)


@pytest.mark.parametrize("text", ["", "  \n ", "一\n二\n三\n四", "**半个强调", "**", "这一行字实在是太长了太长了太长了",
                                  "a\n\nb", "\u200b\u200b"])
def test_parse_text_rejects_bad_text(text):
    with pytest.raises(VideoError):
        parse_text(text)


def test_text_width_counts_chinese_wider_than_latin():
    assert text_em("越推开") == pytest.approx(2.4)
    assert text_em("a") < text_em("越") < text_em("ab")
    assert text_em("“越”") == pytest.approx(2.4)  # 中文引号按全角算
    assert text_em("e\u0301\u200b") == pytest.approx(0.45)  # 组合符号、零宽字符不占宽度


def test_font_sizes_fill_the_width_and_fit_the_height():
    lines = (Line("越怕失去", False), Line("越推开", True))

    normal, emph = font_sizes(lines, Box(0, 0, 800, 900))

    assert emph > normal
    # 错开、立体厚度和描边都要放得下
    assert emph * (text_em("越推开") + 0.15 + 1 / 18 + 2 / 22) <= 800
    assert normal * (text_em("越怕失去") + 0.15) <= 800
    assert (normal + emph) * 1.08 <= 900


def test_font_sizes_shrink_to_fit_a_short_box_and_are_capped():
    lines = (Line("一", False), Line("二", False), Line("三", True))

    assert max(font_sizes(lines, Box(0, 0, 4000, 4000))) <= 330
    assert sum(font_sizes(lines, Box(0, 0, 800, 300))) * 1.08 <= 300


def test_make_thumbnail_probes_colours_then_composes_one_frame(tmp_path):
    runner = FakeRunner(ppm_pixels=[BG, BG, INK, INK, BG])
    out = tmp_path / "thumbnail.jpg"

    make_thumbnail(Path("img/s09.jpg"), out, parse_text("越怕失去\n**越推开**"), LAYOUTS["16x9"],
                   tmp_path / "font's.ttf", tmp_path / "tmp", runner)

    probe, render = runner.ffmpeg_calls()
    assert probe[-1].endswith(".ppm") and "img/s09.jpg" in probe
    graph = _graph(runner)
    assert "color=c=0x3C3CA0:s=1920x1080" in graph
    assert "crop=" in graph and "rotate=" in graph and graph.count("drawtext=") > 2
    assert "fontcolor=0xFFFFFF" in graph and f"fontcolor={palette(BG).accent}" in graph
    assert "font\\\\\\'s.ttf" in graph  # 路径按滤镜参数转义
    assert graph.count("expansion=none") == graph.count("drawtext=")  # 封面字里的 %{...} 不展开
    assert render[-1] == str(out) and render[render.index("-frames:v") + 1] == "1"
    texts = sorted(p.read_text(encoding="utf-8") for p in (tmp_path / "tmp").glob("*.txt"))
    assert texts == ["越怕失去", "越推开"]
    assert not list((tmp_path / "tmp").glob("*.ppm"))


def test_make_thumbnail_uses_the_whole_frame_when_nothing_stands_out(tmp_path):
    runner = FakeRunner(ppm_pixels=[BG, BG])

    make_thumbnail(Path("a.jpg"), tmp_path / "t.jpg", parse_text("hi"), LAYOUTS["3x4"],
                   tmp_path / "f.ttf", tmp_path / "tmp", runner)

    graph = _graph(runner)
    assert "crop=1920:1080:0:0" in graph and "s=1080x1440" in graph
