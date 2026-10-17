from pathlib import Path

import pytest
from video_fakes import FakeRunner, ppm

from stickman.video.compose import Box, build_graph, content_box, measure, parse_ppm
from stickman.video.config import ENCODER, VideoConfig
from stickman.video.errors import VideoError
from stickman.video.motion import GREEN, MAGENTA, Motion, parse_motion
from stickman.video.render import ClipJob, part_path, render_clip
from stickman.video.workdir import WorkDir

K, B = (255, 0, 255), (0, 0, 0)


def _motion(raw):
    motion, errors = parse_motion(raw, "s01")
    assert errors == []
    return motion


def test_ppm_is_parsed_and_content_box_is_scaled_to_the_frame():
    width, height, pixels = parse_ppm(ppm(4, 2, [K, B, K, K, K, K, K, K]))

    assert (width, height) == (4, 2)
    assert content_box(width, height, pixels, MAGENTA) == Box(480, 0, 480, 540)


def test_content_box_is_none_when_everything_is_key_colour():
    assert content_box(2, 1, bytes([255, 0, 255] * 2), MAGENTA) is None


@pytest.mark.parametrize("data", [b"P5\n1 1\n255\nx", b"P6\n2 2\n255\n" + b"\x00" * 5, b"junk"])
def test_bad_probe_output_is_an_error(data):
    with pytest.raises(VideoError):
        parse_ppm(data)


def test_measure_probes_a_small_copy_and_rejects_an_empty_layer(tmp_path):
    probe = tmp_path / "s01.fg.ppm"
    runner = FakeRunner()

    box = measure(Path("fg.jpg"), MAGENTA, probe, runner)

    assert box == Box(0, 0, 1920, 1080)
    cmd = runner.ffmpeg_calls()[0]
    assert cmd[-1] == str(probe) and "-f" in cmd and "image2" in cmd
    with pytest.raises(VideoError) as exc:
        measure(Path("fg.jpg"), MAGENTA, probe, FakeRunner(ppm_pixels=[K]))
    assert "fg.jpg" in str(exc.value)


def _paths(names):
    return {n: Path(f"/l/{n}.jpg") for n in names}


def test_scroll_keeps_the_room_still_and_slides_the_scenery_behind_the_window():
    motion = _motion({"type": "scroll", "layers": {"bg": "a", "scenery": "b", "fg": "c"}, "speed": 900})

    graph = build_graph(motion, _paths(["bg", "scenery", "fg"]), Path("/i.jpg"), 3.0, lambda n, k: None)

    assert graph.inputs == (Path("/l/bg.jpg"), Path("/l/scenery.jpg"), Path("/l/fg.jpg"))
    assert "hstack=3" in graph.graph and "mod(t*900,3840)" in graph.graph
    assert "[0:v]" in graph.graph.split("chromakey=0x00FF00")[0].rsplit(";", 1)[-1]
    assert "chromakey=0xFF00FF" in graph.graph and graph.graph.endswith("[comp]")


def test_scale_eases_the_prop_around_the_characters_feet():
    motion = _motion({"type": "scale", "layers": {"bg": "a", "prop": "b", "fg": "c"}, "from": 0.6, "to": 0.15})
    boxes = {"prop": Box(100, 200, 800, 300), "fg": Box(860, 400, 200, 450)}
    asked = []

    def probe(name, key):
        asked.append((name, key))
        return boxes[name]

    graph = build_graph(motion, _paths(["bg", "prop", "fg"]), Path("/i.jpg"), 4.0, probe)

    assert sorted(asked) == [("fg", MAGENTA), ("prop", GREEN)]
    assert "crop=800:300:100:200" in graph.graph
    assert "1152" in graph.graph and "288" in graph.graph and "t/4.000" in graph.graph
    # 人物不管生成多大，都裁出来缩到画面高度的三分之一，脚踩在 feet（默认 [0.5, 0.79]）上，圆圈以脚为中心。
    assert "crop=200:450:860:400" in graph.graph and "scale=160:360" in graph.graph
    assert "overlay=x='960-w/2':y='853-h/2'" in graph.graph
    assert "overlay=x=880:y='493+" in graph.graph and graph.graph.endswith("[comp]")


def test_scale_feet_can_be_moved():
    motion = _motion({"type": "scale", "layers": {"bg": "a", "prop": "b", "fg": "c"}, "from": 0.6, "to": 0.15,
                      "feet": [0.25, 0.5]})

    graph = build_graph(motion, _paths(["bg", "prop", "fg"]), Path("/i.jpg"), 4.0, lambda n, k: Box(0, 0, 100, 100))

    assert "overlay=x='480-w/2':y='540-h/2'" in graph.graph and "overlay=x=300:y='180+" in graph.graph


def test_rise_starts_the_puffs_at_the_given_spot():
    motion = _motion({"type": "rise", "layers": {"bg": "a", "puff": "b"}, "at": [0.5, 0.5]})

    graph = build_graph(motion, _paths(["bg", "puff"]), Path("/i.jpg"), 4.0, lambda n, k: Box(0, 0, 400, 200))

    assert graph.inputs == (Path("/l/bg.jpg"), Path("/l/puff.jpg"))
    assert "crop=400:200:0:0" in graph.graph and "[st]split[s0][s1]" in graph.graph
    assert "x='960-" in graph.graph and "y='540-" in graph.graph and graph.graph.endswith("[comp]")


def test_glow_pulses_the_given_colour_of_the_single_image():
    motion = _motion({"type": "glow", "color": "#F9E440"})

    graph = build_graph(motion, {}, Path("/i.jpg"), 4.0, lambda n, k: None)

    assert graph.inputs == (Path("/i.jpg"),)
    assert "chromakey=0xF9E440" in graph.graph and graph.graph.endswith("[comp]")


def test_layered_clip_composes_layers_then_shares_the_look_and_subtitle_tail(tmp_path):
    work = WorkDir(tmp_path)
    motion = _motion({"type": "rise", "layers": {"bg": "a", "puff": "b"}, "at": [0.5, 0.6]})
    job = ClipJob("s02", work.image("s02"), 300, 120, work.clip("s02"), motion,
                  (("bg", work.layer("s02", "bg")), ("puff", work.layer("s02", "puff"))))
    runner = FakeRunner()

    render_clip(job, work.subs, VideoConfig(), runner)

    encode = next(c for c in runner.ffmpeg_calls() if ENCODER in c)
    assert encode[-1] == str(part_path(work.clip("s02"))) and work.clip("s02").exists()
    assert [encode[i + 1] for i, a in enumerate(encode) if a == "-i"] == [str(work.layer("s02", "bg")),
                                                                         str(work.layer("s02", "puff"))]
    assert encode[encode.index("-frames:v") + 1] == "120" and encode[encode.index("-map") + 1] == "[v]"
    graph = encode[encode.index("-filter_complex") + 1]
    assert "[comp]rgbashift" in graph and "setpts=PTS-STARTPTS+300/30/TB" in graph
    assert "fade=t=out" not in graph and graph.endswith("format=yuv420p[v]")
    assert "perspective=" not in graph
    probe = next(c for c in runner.ffmpeg_calls() if c[-1].endswith(".ppm"))
    assert str(work.layer("s02", "puff")) in probe


def test_ppm_pixels_that_look_like_whitespace_are_kept():
    width, height, pixels = parse_ppm(ppm(2, 1, [(32, 10, 9), (13, 0, 0)]))

    assert (width, height) == (2, 1) and pixels == bytes([32, 10, 9, 13, 0, 0])


def test_degenerate_layer_shapes_are_rejected(tmp_path):
    # 100x1 探测图里只有一个像素有内容：换算到画面是 19x1080 的细竖条，缩放后尺寸会失控。
    with pytest.raises(VideoError) as exc:
        measure(Path("fg.jpg"), MAGENTA, tmp_path / "p.ppm", FakeRunner(ppm_pixels=[B] + [K] * 99))

    assert "fg.jpg" in str(exc.value)


def test_probe_file_is_removed_after_measuring(tmp_path):
    probe = tmp_path / "s01.fg.ppm"

    measure(Path("fg.jpg"), MAGENTA, probe, FakeRunner())

    assert not probe.exists()


def test_unknown_recipe_kind_is_an_error_not_a_silent_fallback():
    motion = Motion("spin", (), ())

    with pytest.raises(VideoError):
        build_graph(motion, {}, Path("/i.jpg"), 4.0, lambda n, k: Box(0, 0, 10, 10))


def test_glow_tolerance_stays_short_of_white_for_pale_colours():
    # 白头、灰色物体的色度在中性点；容差必须小于颜色到中性点的距离，否则它们会跟着一起亮。
    pale = build_graph(_motion({"type": "glow", "color": "#F9F7A6"}), {}, Path("/i.jpg"), 4.0, lambda n, k: None)
    vivid = build_graph(_motion({"type": "glow", "color": "#F9E440"}), {}, Path("/i.jpg"), 4.0, lambda n, k: None)

    assert "chromakey=0xF9F7A6:0.051:" in pale.graph
    assert "chromakey=0xF9E440:0.108:" in vivid.graph
