import json

import pytest
from video_fakes import FakeImages, FakeRunner, FakeSecrets, FakeTts, write_voice

from stickman.video import cli, pipeline
from stickman.video.config import ENCODER, KEYCHAIN_IMAGE, KEYCHAIN_TTS, VideoConfig
from stickman.video.errors import VideoError
from stickman.video.pipeline import Services, render, reshoot
from stickman.video.render import read_manifest
from stickman.video.text import spoken
from stickman.video.workdir import WorkDir

LINES = ["你好，世界。", "第二个镜头。", "最后一句话。"]
TEXTS = {"s01": "你好，世界。", "s02": "第二个镜头。最后一句话。"}


def _setup(tmp_path):
    work = WorkDir(tmp_path / "w")
    work.root.mkdir()
    work.narration.write_text("\n".join(LINES) + "\n", encoding="utf-8")
    shots = [{"id": "s01", "line": 1, "bg": "蓝色", "scene": "站着"}, {"id": "s02", "line": 2, "bg": "红色", "scene": "走路"}]
    work.shots.write_text(json.dumps({"style": "风格{bg}：", "shots": shots}, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "ref.jpg").write_bytes(b"\xff\xd8\xff")
    (tmp_path / "bgm.mp3").write_bytes(b"mp3")
    config = VideoConfig(ref_image=str(tmp_path / "ref.jpg"), bgm=str(tmp_path / "bgm.mp3"))
    return work, config


def _services(**overrides):
    defaults = dict(runner=FakeRunner(durations={"voice.wav": 8.0}, frames={"s01.mp4": 80, "s02.mp4": 160}),
                    read_secret=FakeSecrets(), synthesize=FakeTts(), generate_image=FakeImages())
    return Services(**{**defaults, **overrides})


def _cache(work, sid):
    work.img_dir.mkdir(exist_ok=True)
    work.image(sid).write_bytes(b"jpg-" + sid.encode())
    write_voice(work, sid, spoken(TEXTS[sid]), source_text=TEXTS[sid])


def test_render_generates_only_missing_media_and_writes_everything(tmp_path):
    work, config = _setup(tmp_path)
    _cache(work, "s01")
    services = _services()
    out = work.root / "out.mp4"

    report = render(work, config, services, out=out, offline=False)

    assert list(services.generate_image.prompts) == ["s02"]
    assert services.generate_image.prompts["s02"] == "风格红色：走路"
    assert services.synthesize.texts == [TEXTS["s02"]]
    assert sorted(services.read_secret.services) == sorted([KEYCHAIN_IMAGE, KEYCHAIN_TTS])
    assert report.media.images_generated == 1 and report.media.billed_audio_s > 0
    assert out.exists() and work.subs.exists() and work.clip("s02").exists()
    manifest = read_manifest(work)
    assert manifest["frames"] == {"s01": 80, "s02": 160} and set(manifest["images"]) == {"s01", "s02"}
    timings = json.loads(work.timings.read_text(encoding="utf-8"))
    assert set(timings["timings_s"]) == {"media", "voice", "clips", "final", "total"}
    assert timings["video_s"] == 8.0 and timings["images_generated"] == 1


def test_render_with_everything_cached_never_reads_keys(tmp_path):
    work, config = _setup(tmp_path)
    _cache(work, "s01")
    _cache(work, "s02")
    services = _services()

    render(work, config, services, out=work.root / "out.mp4", offline=False)

    assert services.read_secret.services == [] and services.synthesize.texts == []


def test_offline_render_lists_missing_media_without_touching_keys(tmp_path):
    work, config = _setup(tmp_path)
    _cache(work, "s01")
    services = _services()

    with pytest.raises(VideoError) as exc:
        render(work, config, services, out=work.root / "out.mp4", offline=True)

    assert "s02" in str(exc.value) and services.read_secret.services == []


def test_failed_jobs_do_not_stop_others_and_a_rerun_resumes(tmp_path):
    work, config = _setup(tmp_path)
    images, voices = FakeImages(fail_ids={"s01"}), FakeTts()
    services = _services(generate_image=images, synthesize=voices)

    with pytest.raises(VideoError) as exc:
        render(work, config, services, out=work.root / "out.mp4", offline=False)

    assert "s01" in str(exc.value)
    assert work.image("s02").exists() and work.voice("s01").exists() and work.voice("s02").exists()
    images.prompts.clear()
    render(work, config, services, out=work.root / "out.mp4", offline=False)
    assert list(images.prompts) == ["s01"] and len(voices.texts) == 2


def test_stale_cached_voice_is_reported_not_rebilled(tmp_path):
    work, config = _setup(tmp_path)
    _cache(work, "s01")
    work.voice_meta("s01").write_text(json.dumps({"source_text": "旧的旁白"}), encoding="utf-8")
    services = _services()

    with pytest.raises(VideoError) as exc:
        render(work, config, services, out=work.root / "out.mp4", offline=False)

    assert "s01" in str(exc.value) and services.synthesize.texts == [] and services.read_secret.services == []


def test_missing_bgm_or_reference_image_is_reported_before_any_call(tmp_path):
    work, config = _setup(tmp_path)
    services = _services()

    with pytest.raises(VideoError):
        render(work, VideoConfig(ref_image=config.ref_image, bgm=str(tmp_path / "none.mp3")), services,
               out=work.root / "o.mp4", offline=False)
    with pytest.raises(VideoError):
        render(work, VideoConfig(ref_image=str(tmp_path / "none.jpg"), bgm=config.bgm), services,
               out=work.root / "o.mp4", offline=False)
    assert services.read_secret.services == []


def _rendered(tmp_path, **overrides):
    work, config = _setup(tmp_path)
    _cache(work, "s01")
    _cache(work, "s02")
    services = _services(**overrides)
    render(work, config, services, out=work.root / "out.mp4", offline=False)
    services.runner.calls.clear()
    return work, config, services


def _encoded(runner):
    return [c[-1].replace(".part.mp4", ".mp4") for c in runner.ffmpeg_calls() if ENCODER in c]


def test_reshoot_regenerates_listed_shots_and_rerenders_only_their_clips(tmp_path):
    work, config, services = _rendered(tmp_path)
    old = work.image("s02").read_bytes()

    reshoot(work, config, services, ("s02",), out=work.root / "out.mp4", generate=True)

    assert (work.rejected_dir / "s02.1.jpg").read_bytes() == old
    assert list(services.generate_image.prompts) == ["s02"]
    assert _encoded(services.runner) == [str(work.clip("s02"))]
    assert services.runner.ffmpeg_calls()[-1][-1] == str(work.root / "out.part.mp4")
    assert read_manifest(work)["images"]["s02"] != read_manifest(work)["images"]["s01"]
    reshoot(work, config, services, ("s02",), out=work.root / "out.mp4", generate=True)
    assert (work.rejected_dir / "s02.2.jpg").exists()


def test_reshoot_without_generate_makes_no_api_calls(tmp_path):
    work, config, services = _rendered(tmp_path)

    reshoot(work, config, services, ("s01",), out=work.root / "out.mp4", generate=False)

    assert services.generate_image.prompts == {} and services.read_secret.services == []
    assert _encoded(services.runner) == [str(work.clip("s01"))]
    assert not work.rejected_dir.exists()


@pytest.mark.parametrize("ids", [("s09",), ("../x",)])
def test_reshoot_rejects_unknown_ids_before_any_call(tmp_path, ids):
    work, config, services = _rendered(tmp_path)

    with pytest.raises(VideoError):
        reshoot(work, config, services, ids, out=work.root / "out.mp4", generate=True)

    assert services.generate_image.prompts == {} and services.runner.calls == []


@pytest.mark.parametrize("change", [{"frames": {"s01": 81, "s02": 160}}, {"bitrate": "14M"}, {"encoder": "libx264"}])
def test_reshoot_refuses_when_the_timeline_or_encoding_changed(tmp_path, change):
    work, config, services = _rendered(tmp_path)
    manifest = {**read_manifest(work), **change}
    work.manifest.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(VideoError):
        reshoot(work, config, services, ("s01",), out=work.root / "out.mp4", generate=False)

    assert _encoded(services.runner) == []


def test_reshoot_checks_the_new_clip_frame_count(tmp_path):
    work, config, services = _rendered(tmp_path)
    services.runner.frames["s01.mp4"] = 79

    with pytest.raises(VideoError) as exc:
        reshoot(work, config, services, ("s01",), out=work.root / "out.mp4", generate=False)

    assert "s01" in str(exc.value)


def test_reshoot_needs_a_previous_render(tmp_path):
    work, config = _setup(tmp_path)
    _cache(work, "s01")
    _cache(work, "s02")

    with pytest.raises(VideoError):
        reshoot(work, config, _services(), ("s01",), out=work.root / "out.mp4", generate=False)


def test_cli_render_and_reshoot(tmp_path, monkeypatch, capsys):
    work, config = _setup(tmp_path)
    _cache(work, "s01")
    _cache(work, "s02")
    monkeypatch.chdir(tmp_path)
    services = _services()
    monkeypatch.setattr(cli, "_services", lambda: services)
    flags = ["--ref", "ref.jpg", "--bgm", "bgm.mp3"]

    assert cli.main(["render", "w", "--offline", *flags]) == 0
    out = capsys.readouterr().out
    assert "w.mp4" in out and "8.0" in out
    assert (tmp_path / "w" / "w.mp4").exists()
    assert cli.main(["reshoot", "w", "s01", "--no-generate", *flags]) == 0
    assert cli.main(["reshoot", "w", "s01", "--no-generate", "--out", "../x.mp4", *flags]) == 1
    assert cli.main(["render", "../outside"]) == 1
    assert cli.main(["reshoot", "w", "s01", "--no-generate", "--out", "w/shots.json", *flags]) == 1
    assert cli.main(["reshoot", "w", "s01", "--no-generate", "--out", "other.mp4", *flags]) == 1
    assert cli.main(["reshoot", "w", "s01", "--no-generate", "--out", "w/v2.mp4", *flags]) == 0
    (tmp_path.parent / "secret.jpg").write_bytes(b"\xff\xd8\xff")
    (tmp_path / "link.jpg").symlink_to(tmp_path.parent / "secret.jpg")
    assert cli.main(["reshoot", "w", "s01", "--out", "w/v3.mp4", "--ref", "link.jpg", "--bgm", "bgm.mp3"]) == 1
    (tmp_path / "bad=name").mkdir()
    assert cli.main(["render", "bad=name", "--offline", *flags]) == 1


def test_default_services_use_real_clients():
    services = pipeline.Services()

    assert services.runner.__name__ == "run_command" and services.read_secret.__name__ == "read_secret"


def test_reshoot_rerun_after_partial_failure_does_not_pay_twice(tmp_path):
    work, config, services = _rendered(tmp_path, generate_image=FakeImages(fail_ids={"s02"}))

    with pytest.raises(VideoError):
        reshoot(work, config, services, ("s01", "s02"), out=work.root / "out.mp4", generate=True)
    services.generate_image.prompts.clear()
    reshoot(work, config, services, ("s01", "s02"), out=work.root / "out.mp4", generate=True)

    assert list(services.generate_image.prompts) == ["s02"]
    assert sorted(p.name for p in work.rejected_dir.iterdir()) == ["s01.1.jpg", "s02.1.jpg"]


def test_reshoot_fails_before_moving_images_when_keys_are_unavailable(tmp_path):
    work, config, services = _rendered(tmp_path)

    def no_key(service):
        raise VideoError(f"钥匙串里读不到 {service}")

    broken = Services(runner=services.runner, read_secret=no_key,
                      synthesize=services.synthesize, generate_image=services.generate_image)
    with pytest.raises(VideoError):
        reshoot(work, config, broken, ("s02",), out=work.root / "out.mp4", generate=True)

    assert work.image("s02").exists() and not work.rejected_dir.exists()


def test_unexpected_worker_errors_are_collected_with_the_rest(tmp_path):
    work, config = _setup(tmp_path)

    def broken_image(*args, **kwargs):
        raise KeyError("data")

    services = _services(generate_image=broken_image)
    with pytest.raises(VideoError) as exc:
        render(work, config, services, out=work.root / "out.mp4", offline=False)

    assert "KeyError" in str(exc.value) and work.voice("s01").exists() and work.voice("s02").exists()


RISE = {"type": "rise", "layers": {"bg": "桌上一口锅", "puff": "一缕蒸汽"}, "at": [0.5, 0.6]}
TEXTS3 = dict(zip(("s01", "s02", "s03"), LINES))


def _layered(tmp_path, cache=True, **overrides):
    """三个镜头，s02 是分层镜头（rise：bg + puff）。cache=True 时先完整渲染一遍。"""
    work, config = _setup(tmp_path)
    shots = [{"id": f"s0{i}", "line": i, "bg": "蓝色", "scene": "站着"} for i in (1, 2, 3)]
    shots[1]["motion"] = RISE
    work.shots.write_text(json.dumps({"style": "风格{bg}：", "shots": shots}, ensure_ascii=False), encoding="utf-8")
    for sid, text in TEXTS3.items():
        write_voice(work, sid, spoken(text), source_text=text)
    services = _services(runner=FakeRunner(durations={"voice.wav": 8.0}), **overrides)
    if cache:
        render(work, config, services, out=work.root / "out.mp4", offline=False)
        services.runner.frames.update({f"{sid}.mp4": n for sid, n in read_manifest(work)["frames"].items()})
        services.runner.calls.clear()
        services.generate_image.prompts.clear()
    return work, config, services


def test_render_generates_layers_instead_of_a_whole_frame_for_layered_shots(tmp_path):
    work, config, services = _layered(tmp_path, cache=False)

    report = render(work, config, services, out=work.root / "out.mp4", offline=False)

    assert sorted(services.generate_image.prompts) == ["s01", "s02:bg", "s02:puff", "s03"]
    assert services.generate_image.prompts["s02:puff"].endswith("没有任何其他物体。")
    assert report.media.images_generated == 4 and not work.image("s02").exists()
    assert work.layer("s02", "bg").exists() and work.layer("s02", "puff").exists()
    assert set(read_manifest(work)["images"]) == {"s01", "s02:bg", "s02:puff", "s03"}
    layered = [c for c in services.runner.ffmpeg_calls() if "-filter_complex" in c and ENCODER in c]
    assert [c[-1] for c in layered] == [str(work.clip("s02")).replace(".mp4", ".part.mp4")]


def test_offline_render_names_the_missing_layer(tmp_path):
    work, config, services = _layered(tmp_path)
    work.layer("s02", "puff").unlink()

    with pytest.raises(VideoError) as exc:
        render(work, config, services, out=work.root / "out.mp4", offline=True)

    assert "s02:puff" in str(exc.value)


def test_reshoot_one_layer_regenerates_only_that_layer(tmp_path):
    work, config, services = _layered(tmp_path)
    old = work.layer("s02", "puff").read_bytes()

    reshoot(work, config, services, ("s02:puff",), out=work.root / "out.mp4", generate=True)

    assert list(services.generate_image.prompts) == ["s02:puff"]
    assert (work.rejected_dir / "s02-puff.1.jpg").read_bytes() == old
    assert _encoded(services.runner) == [str(work.clip("s02"))]


def test_reshoot_a_layered_shot_regenerates_all_its_layers(tmp_path):
    work, config, services = _layered(tmp_path)

    reshoot(work, config, services, ("s02", "s02:bg"), out=work.root / "out.mp4", generate=True)

    assert sorted(services.generate_image.prompts) == ["s02:bg", "s02:puff"]
    assert _encoded(services.runner) == [str(work.clip("s02"))]


@pytest.mark.parametrize("ids", [("s01:fg",), ("s02:../x",), ("s02:sky",), ("s09:bg",), ("s02:",)])
def test_reshoot_rejects_unknown_layers_before_any_call(tmp_path, ids):
    work, config, services = _layered(tmp_path)

    with pytest.raises(VideoError):
        reshoot(work, config, services, ids, out=work.root / "out.mp4", generate=True)

    assert services.generate_image.prompts == {} and services.runner.calls == []
