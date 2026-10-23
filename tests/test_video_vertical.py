"""抖音竖屏版（9x16）：共用横版的图、配音和字幕时间，只把画面重新排进 1080x1920，顶部常驻封面字。"""

import json

import pytest
from video_fakes import FakeImages, FakeRunner, FakeSecrets, FakeTts, write_voice

from stickman.video import cli
from stickman.video.config import ENCODER, VideoConfig
from stickman.video.errors import VideoError
from stickman.video.motion import parse_motion
from stickman.video.pipeline import Services, render, reshoot
from stickman.video.render import clip_jobs, part_path, read_manifest, render_clips, vertical_graph
from stickman.video.shots import Shot
from stickman.video.lang import EN
from stickman.video.subtitles import SUB_STYLE_9X16, SUB_STYLE_EN_9X16, ass_document
from stickman.video.text import spoken
from stickman.video.thumbnail import LAYOUTS, make_headline, parse_text
from stickman.video.workdir import WorkDir

LINES = ["你好，世界。", "第二个镜头。", "最后一句话。"]
TEXTS = {"s01": "你好，世界。", "s02": "第二个镜头。最后一句话。"}
PUBLISH = {"title": "标题", "description": "简介",
           "sources": [{"author": "索伦·克尔凯郭尔", "title": "日记 JJ", "kind": "book", "link": ""}],
           "thumbnail": {"shot": "s01", "text": "分开以后\n**还在倒放**"}}


# ---------- 工作目录


def test_workdir_9x16_has_its_own_clips_subs_and_output_but_shares_voice_and_images(tmp_path):
    wide, tall = WorkDir(tmp_path / "slug"), WorkDir(tmp_path / "slug", fmt="9x16")

    assert tall.clip("s01").parent.name == "clips_9x16" and tall.subs.name == "subs.9x16.ass"
    assert (tall.clip_list.name, tall.manifest.name, tall.timings.name) == (
        "clips.9x16.txt", "render.9x16.json", "timings.9x16.json")
    assert tall.default_output().name == "slug.9x16.mp4" and tall.headline.name == "headline.9x16.png"
    assert (tall.voice("s01"), tall.voice_wav, tall.trimmed("s01")) == (wide.voice("s01"), wide.voice_wav, wide.trimmed("s01"))
    assert (tall.image("s01"), tall.publish, tall.thumbnail("3x4")) == (wide.image("s01"), wide.publish, wide.thumbnail("3x4"))
    assert wide.default_output().name == "slug.mp4" and wide.subs.name == "subs.ass"


def test_workdir_9x16_in_english_keeps_both_suffixes(tmp_path):
    en = WorkDir(tmp_path / "slug", "en", "9x16")

    assert en.default_output().name == "slug.en.9x16.mp4" and en.clip("s01").parent.name == "clips_en_9x16"
    assert en.voice("s01").parent.name == "voice_en" and en.publish.name == "publish.en.json"


def test_workdir_rejects_unknown_format(tmp_path):
    with pytest.raises(VideoError):
        WorkDir(tmp_path, fmt="1x1")


# ---------- 字幕、画面


def test_vertical_subtitles_use_a_portrait_canvas():
    doc = ass_document(((0.0, 1.0, "你好"),), SUB_STYLE_9X16, (1080, 1920))

    assert "PlayResX: 1080\nPlayResY: 1920" in doc and SUB_STYLE_9X16 in doc


def test_vertical_graph_crops_the_middle_and_extends_the_edges():
    graph = vertical_graph("fit", "vert")

    assert graph.startswith("[fit]crop=1440:1080:240:0,scale=1080:810")
    assert graph.count("gblur=") == 2
    # 回归：一行像素拉成 555 行时 scale 会改 SAR，后面的 vignette 按 SAR 算暗角，整帧被压黑。拼好后要把 SAR 归一。
    assert graph.endswith("vstack=3,setsar=1[vert]")


def _shots(*ids):
    return tuple(Shot(sid, i + 1, "蓝色", "站着") for i, sid in enumerate(ids))


def test_render_clips_9x16_bakes_a_portrait_still_and_overlays_the_headline(tmp_path):
    work = WorkDir(tmp_path, fmt="9x16")
    jobs = clip_jobs(work, _shots("s01"), (0, 82))
    runner = FakeRunner()

    render_clips(jobs, work.subs, VideoConfig(), runner, headline=work.headline)

    still, encode = runner.ffmpeg_calls()
    assert "vstack=3" in still[still.index("-filter_complex") + 1]
    graph = encode[encode.index("-filter_complex") + 1]
    assert str(work.headline) in encode and "perspective=" in graph
    assert graph.index("perspective=") < graph.index("overlay=") < graph.index("subtitles=")
    assert encode[encode.index("-frames:v") + 1] == "82" and encode[-1] == str(part_path(work.clip("s01")))


def test_render_clips_9x16_composes_layered_shots_before_reframing(tmp_path):
    work = WorkDir(tmp_path, fmt="9x16")
    motion, errors = parse_motion({"type": "glow", "color": "#FFD700"}, "s01")
    assert not errors
    runner = FakeRunner()

    render_clips(clip_jobs(work, (Shot("s01", 1, "蓝色", "站着", motion),), (0, 60)), work.subs, VideoConfig(),
                 runner, headline=work.headline)

    (encode,) = runner.ffmpeg_calls()
    graph = encode[encode.index("-filter_complex") + 1]
    assert graph.index("[comp]") < graph.index("vstack=3") < graph.index("[hl]overlay=") < graph.index("subtitles=")
    assert str(work.headline) in encode


# ---------- 封面字


def test_make_headline_draws_the_cover_text_on_a_transparent_png(tmp_path):
    runner = FakeRunner(ppm_pixels=[(60, 60, 160)] * 2)
    out = tmp_path / "headline.9x16.png"

    make_headline(tmp_path / "s01.jpg", out, parse_text("分开以后\n**还在倒放**"), tmp_path / "f.ttf",
                  tmp_path / "tmp", runner)

    probe, draw = runner.ffmpeg_calls()
    assert probe[-1].endswith(".ppm")
    assert draw[draw.index("-f") + 1] == "lavfi" and "rotate=" in draw[draw.index("-i") + 1]
    assert draw[-1] == str(out) and out.exists()


def test_there_is_a_full_screen_portrait_cover_layout():
    assert (LAYOUTS["9x16"].width, LAYOUTS["9x16"].height) == (1080, 1920)


# ---------- 整条流水线


def _setup(tmp_path):
    wide = WorkDir(tmp_path / "w")
    wide.root.mkdir()
    wide.narration.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    shots = [{"id": "s01", "line": 1, "bg": "蓝色", "scene": "站着"}, {"id": "s02", "line": 2, "bg": "红色", "scene": "走路"}]
    wide.shots.write_text(json.dumps({"style": "风格{bg}：", "shots": shots}, ensure_ascii=False), encoding="utf-8")
    wide.publish.write_text(json.dumps(PUBLISH, ensure_ascii=False), encoding="utf-8")
    wide.img_dir.mkdir()
    for sid in ("s01", "s02"):
        wide.image(sid).write_bytes(b"jpg-" + sid.encode())
        write_voice(wide, sid, spoken(TEXTS[sid]), source_text=TEXTS[sid])
    for name in ("ref.jpg", "bgm.mp3", "font.ttf"):
        (tmp_path / name).write_bytes(b"x")
    config = VideoConfig(ref_image=str(tmp_path / "ref.jpg"), bgm=str(tmp_path / "bgm.mp3"),
                         thumb_font=str(tmp_path / "font.ttf"))
    return wide, WorkDir(wide.root, fmt="9x16"), config


def _services():
    return Services(runner=FakeRunner(durations={"voice.wav": 8.0}, frames={"s01.mp4": 80, "s02.mp4": 160}),
                    read_secret=FakeSecrets(), synthesize=FakeTts(), generate_image=FakeImages())


def test_render_9x16_reuses_the_wide_cut_without_any_api_call(tmp_path):
    wide, tall, config = _setup(tmp_path)
    render(wide, config, _services(), out=wide.default_output(), offline=False)
    services = _services()

    report = render(tall, config, services, out=tall.default_output(), offline=False)

    assert services.read_secret.services == [] and services.synthesize.texts == []
    assert services.generate_image.prompts == {}
    assert report.out.name == "w.9x16.mp4" and report.out.exists() and tall.headline.exists()
    assert SUB_STYLE_9X16 in tall.subs.read_text(encoding="utf-8")
    assert tall.clip("s01").exists() and read_manifest(tall)["frames"] == read_manifest(wide)["frames"]
    assert read_manifest(tall)["looks"] == read_manifest(wide)["looks"]


def test_render_9x16_requires_the_wide_cut_first(tmp_path):
    _, tall, config = _setup(tmp_path)

    with pytest.raises(VideoError, match="中文版"):
        render(tall, config, _services(), out=tall.default_output(), offline=False)


def test_render_9x16_never_voices_missing_lines(tmp_path):
    wide, tall, config = _setup(tmp_path)
    render(wide, config, _services(), out=wide.default_output(), offline=False)
    wide.voice("s02").unlink()
    services = _services()

    with pytest.raises(VideoError, match="s02"):
        render(tall, config, services, out=tall.default_output(), offline=False)

    assert services.synthesize.texts == [] and services.read_secret.services == []


def test_render_9x16_needs_the_cover_text(tmp_path):
    wide, tall, config = _setup(tmp_path)
    render(wide, config, _services(), out=wide.default_output(), offline=False)
    wide.publish.unlink()

    with pytest.raises(VideoError, match="publish.json"):
        render(tall, config, _services(), out=tall.default_output(), offline=False)


def test_render_9x16_needs_the_cover_font(tmp_path):
    wide, tall, config = _setup(tmp_path)
    render(wide, config, _services(), out=wide.default_output(), offline=False)
    (tmp_path / "font.ttf").unlink()

    with pytest.raises(VideoError, match="得意黑"):
        render(tall, config, _services(), out=tall.default_output(), offline=False)


def test_reshoot_9x16_only_rerenders(tmp_path):
    wide, tall, config = _setup(tmp_path)
    render(wide, config, _services(), out=wide.default_output(), offline=False)
    services = _services()
    render(tall, config, services, out=tall.default_output(), offline=False)

    with pytest.raises(VideoError, match="--no-generate"):
        reshoot(tall, config, services, ("s01",), out=tall.default_output(), generate=True)

    report = reshoot(tall, config, services, ("s01",), out=tall.default_output(), generate=False)
    assert report.out.exists() and services.generate_image.prompts == {}


def test_reshoot_9x16_refuses_when_the_cover_text_changed(tmp_path):
    # 只重渲几个镜头时，新画的封面字只会出现在这几段里，整条视频顶部的字就前后不一样了。
    wide, tall, config = _setup(tmp_path)
    render(wide, config, _services(), out=wide.default_output(), offline=False)
    render(tall, config, _services(), out=tall.default_output(), offline=False)
    wide.publish.write_text(json.dumps({**PUBLISH, "thumbnail": {"shot": "s01", "text": "新的封面字"}},
                                       ensure_ascii=False), encoding="utf-8")

    with pytest.raises(VideoError, match="封面字"):
        reshoot(tall, config, _services(), ("s01",), out=tall.default_output(), generate=False)


def test_render_9x16_in_english_uses_the_english_voice_and_cover_text(tmp_path):
    wide, _, config = _setup(tmp_path)
    render(wide, config, _services(), out=wide.default_output(), offline=False)
    en_wide, en_tall = WorkDir(wide.root, "en"), WorkDir(wide.root, "en", "9x16")
    en_wide.narration.write_text("Hello, world.\nThe second shot.\nOne last line.\n", encoding="utf-8")
    for sid, text in (("s01", "Hello, world."), ("s02", "The second shot. One last line.")):
        write_voice(en_wide, sid, text, source_text=text, lang=EN)
    en_wide.publish.write_text(json.dumps({**PUBLISH, "thumbnail": {"shot": "s02", "text": "**GROW**"}}),
                               encoding="utf-8")
    services = Services(runner=FakeRunner(durations={"voice.en.wav": 8.0}), read_secret=FakeSecrets(),
                        synthesize=FakeTts(), generate_image=FakeImages())

    report = render(en_tall, config, services, out=en_tall.default_output(), offline=False)

    assert report.out.name == "w.en.9x16.mp4" and services.synthesize.texts == []
    assert SUB_STYLE_EN_9X16 in en_tall.subs.read_text(encoding="utf-8")
    assert en_tall.clip("s01").parent.name == "clips_en_9x16"


def test_cli_render_9x16(tmp_path, monkeypatch, capsys):
    wide, _, _ = _setup(tmp_path)
    root = tmp_path / "data" / "videos" / "v1"
    root.parent.mkdir(parents=True)
    wide.root.rename(root)
    for name in ("ref.jpg", "bgm.mp3", "font.ttf"):
        (tmp_path / name).rename(root / name)
    (root / "video.json").write_text(json.dumps({"ref_image": "data/videos/v1/ref.jpg", "bgm": "data/videos/v1/bgm.mp3",
                                                 "thumb_font": "data/videos/v1/font.ttf"}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    runner = FakeRunner(durations={"voice.wav": 8.0}, frames={"s01.mp4": 80, "s02.mp4": 160})
    monkeypatch.setattr(cli, "_services", lambda: cli.pipeline.Services(runner=runner))

    assert cli.main(["render", "data/videos/v1"]) == 0
    assert cli.main(["render", "data/videos/v1", "--format", "9x16"]) == 0
    assert "v1.9x16.mp4" in capsys.readouterr().out
    assert cli.main(["render", "data/videos/v1", "--format", "9x16", "--out", "data/videos/v1/v1.mp4"]) == 1
    assert cli.main(["render", "data/videos/v1", "--out", "data/videos/v1/v1.en.9x16.mp4"]) == 1
    assert any(ENCODER in c for c in runner.ffmpeg_calls())
