import json

import pytest

from stickman.video import cli
from stickman.video.config import IMAGE_MODELS, VideoConfig, load_config
from stickman.video.errors import VideoError
from stickman.video.shots import load_spec, shot_lines
from stickman.video.workdir import WorkDir

LINES = ["第一句。", "第二句，", "第三句。", "第四句。"]
STYLE = "画风，背景是{bg}。画面内容："


def _shot(sid, line, bg="蓝色", scene="火柴人站着"):
    return {"id": sid, "line": line, "bg": bg, "scene": scene}


def _work(tmp_path, shots, style=STYLE, lines=LINES):
    (tmp_path / "narration.txt").write_text("\n".join(lines) + "\n\n", encoding="utf-8")
    (tmp_path / "shots.json").write_text(json.dumps({"style": style, "shots": shots}, ensure_ascii=False), encoding="utf-8")
    return WorkDir(tmp_path)


def test_valid_spec_loads_shots_and_prompts(tmp_path):
    work = _work(tmp_path, [_shot("s01", 1), {**_shot("s02", 3), "cue": "备注"}])

    spec, lines = load_spec(work)

    assert lines == tuple(LINES)
    assert [s.id for s in spec.shots] == ["s01", "s02"]
    assert spec.prompt(spec.shots[1]) == "画风，背景是蓝色。画面内容：火柴人站着"
    assert shot_lines(spec, lines) == (("s01", ("第一句。", "第二句，")), ("s02", ("第三句。", "第四句。")))


@pytest.mark.parametrize(
    "shots, needle",
    [
        ([_shot("s01", 2)], "第 1 行"),
        ([_shot("s01", 1), _shot("s01", 3)], "重复"),
        ([_shot("s01", 1), _shot("s02", 1)], "递增"),
        ([_shot("s01", 1), _shot("s02", 9)], "超出"),
        ([_shot("s01", 1), _shot("s02", "3")], "整数"),
        ([_shot("s01", 1), _shot("s02", True)], "整数"),
        ([_shot("s01", 1, bg="")], "bg"),
        ([_shot("s01", 1, scene=" ")], "scene"),
        ([_shot("../x", 1)], "不合法"),
        ([], "没有镜头"),
        ([_shot("s01", 1), "oops"], "对象"),
        ([_shot("s01", 1), {**_shot("s02", 2), "id": ["s02"]}], "不合法"),
    ],
)
def test_invalid_specs_are_rejected_with_the_shot_named(tmp_path, shots, needle):
    work = _work(tmp_path, shots)

    with pytest.raises(VideoError) as exc:
        load_spec(work)

    assert needle in str(exc.value)


def test_all_errors_are_reported_together(tmp_path):
    work = _work(tmp_path, [_shot("s01", 1, bg=""), _shot("s02", 3, scene="")])

    with pytest.raises(VideoError) as exc:
        load_spec(work)

    assert "s01" in str(exc.value) and "s02" in str(exc.value)


@pytest.mark.parametrize("style", ["没有占位符", "{bg} 和 {other}", "{bg} 和 {", 3])
def test_style_must_be_a_template_with_only_bg(tmp_path, style):
    work = _work(tmp_path, [_shot("s01", 1)], style=style)

    with pytest.raises(VideoError):
        load_spec(work)


def test_missing_or_broken_files_are_reported(tmp_path):
    with pytest.raises(VideoError):
        load_spec(WorkDir(tmp_path))
    (tmp_path / "narration.txt").write_text("一句。\n", encoding="utf-8")
    (tmp_path / "shots.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(VideoError):
        load_spec(WorkDir(tmp_path))
    (tmp_path / "shots.json").write_text("[]", encoding="utf-8")
    with pytest.raises(VideoError):
        load_spec(WorkDir(tmp_path))


def test_check_command_prints_line_ranges(tmp_path, capsys):
    _work(tmp_path, [_shot("s01", 1), _shot("s02", 3)])

    assert cli.main(["check", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "s01" in out and "1–2" in out and "3–4" in out


def test_check_command_fails_on_unfilled_skeleton(tmp_path, capsys):
    _work(tmp_path, [_shot("s01", 1, bg="", scene="")])

    assert cli.main(["check", str(tmp_path)]) == 1
    assert "s01" in capsys.readouterr().err


def test_config_defaults_file_overrides_and_flags(tmp_path):
    assert load_config(tmp_path) == VideoConfig()
    (tmp_path / "video.json").write_text('{"speaker": "S_other", "img_workers": 2}', encoding="utf-8")

    config = load_config(tmp_path, {"img_workers": 3, "bgm": None})

    assert (config.speaker, config.img_workers, config.bgm) == ("S_other", 3, VideoConfig().bgm)


@pytest.mark.parametrize(
    "content", ['{"api_key": "x"}', '{"img_workers": "8"}', '{"img_workers": 0}', '{"img_workers": true}', "[1]", "{bad"]
)
def test_config_file_rejects_unknown_keys_and_bad_values(tmp_path, content):
    (tmp_path / "video.json").write_text(content, encoding="utf-8")

    with pytest.raises(VideoError):
        load_config(tmp_path)


def test_config_image_model_must_be_one_of_the_listed_models(tmp_path):
    (tmp_path / "video.json").write_text('{"image_model": "doubao-seedream-5-0-flash-260915"}', encoding="utf-8")
    assert load_config(tmp_path).image_model == "doubao-seedream-5-0-flash-260915"
    assert VideoConfig().image_model == IMAGE_MODELS[0] == "doubao-seedream-5-0-pro-260628"

    with pytest.raises(VideoError) as exc:
        load_config(tmp_path, {"image_model": "doubao-seedream-4-0"})
    assert "doubao-seedream-5-0-flash-260915" in str(exc.value)


def test_cli_image_model_flag_picks_the_model(tmp_path, monkeypatch):
    (tmp_path / "w").mkdir()
    work = _work(tmp_path / "w", [_shot("s01", 1)])
    for name in ("ref.jpg", "bgm.mp3"):
        (tmp_path / name).write_bytes(b"x")
    monkeypatch.chdir(tmp_path)
    parser = cli.build_parser("stickman video")
    base = ["render", work.root.name, "--ref", "ref.jpg", "--bgm", "bgm.mp3"]

    args = parser.parse_args([*base, "--image-model", "doubao-seedream-5-0-flash-260915"])
    assert cli._prepare(args)[1].image_model == "doubao-seedream-5-0-flash-260915"
    assert cli._prepare(parser.parse_args(base))[1].image_model == IMAGE_MODELS[0]
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--image-model", "other"])


GLOW = {"type": "glow", "color": "#F9E440"}
RISE = {"type": "rise", "layers": {"bg": "桌上一口锅", "puff": "一缕蒸汽"}, "at": [0.5, 0.6]}


def test_motion_is_optional_and_parsed_per_shot(tmp_path):
    work = _work(tmp_path, [_shot("s01", 1), {**_shot("s02", 2), "motion": GLOW}, _shot("s03", 3)])

    spec, _ = load_spec(work)

    assert [s.motion.kind if s.motion else None for s in spec.shots] == [None, "glow", None]


def test_bad_motion_is_reported_with_the_other_errors(tmp_path):
    work = _work(tmp_path, [{**_shot("s01", 1), "motion": {"type": "zoom"}}, _shot("s02", 3, bg="")])

    with pytest.raises(VideoError) as exc:
        load_spec(work)

    assert "s01" in str(exc.value) and "type" in str(exc.value) and "s02" in str(exc.value)


def test_more_than_a_third_of_the_shots_layered_is_rejected(tmp_path):
    work = _work(tmp_path, [{**_shot("s01", 1), "motion": GLOW}, {**_shot("s02", 2), "motion": GLOW}, _shot("s03", 3)])

    with pytest.raises(VideoError) as exc:
        load_spec(work)

    assert "上限 1" in str(exc.value) and "s01" in str(exc.value) and "s02" in str(exc.value)


def test_image_targets_are_whole_frames_for_sway_and_glow_and_layers_otherwise(tmp_path):
    work = _work(tmp_path, [{**_shot("s01", 1), "motion": RISE}, _shot("s02", 2), _shot("s03", 3)])
    spec, _ = load_spec(work)

    targets = spec.targets(work)

    assert [t.id for t in targets] == ["s01:bg", "s01:puff", "s02", "s03"]
    assert targets[0].path == work.layer("s01", "bg") and targets[2].path == work.image("s02")
    assert targets[0].prompt.startswith("画风，背景是蓝色。画面内容：桌上一口锅")
    assert targets[2].prompt == "画风，背景是蓝色。画面内容：火柴人站着"
    assert {t.shot_id for t in targets} == {"s01", "s02", "s03"}


def test_check_command_marks_layered_shots_and_the_cap(tmp_path, capsys):
    _work(tmp_path, [_shot("s01", 1), {**_shot("s02", 2), "motion": RISE}, _shot("s03", 3)])

    assert cli.main(["check", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "分层：rise（bg, puff）" in out and "分层镜头 1 个（上限 1）" in out
    assert "要出图 4 张，缺 4 张" in out
