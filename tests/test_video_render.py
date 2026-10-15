import json

import pytest
from video_fakes import FakeRunner

from stickman.video.config import ENCODER, VideoConfig
from stickman.video.errors import VideoError
from stickman.video.render import (
    clip_filter,
    clip_jobs,
    final_mix,
    image_digest,
    part_path,
    read_manifest,
    render_clips,
    write_manifest,
)
from stickman.video.shots import Shot
from stickman.video.workdir import WorkDir


def _shots(*ids):
    return tuple(Shot(sid, i + 1, "蓝色", "站着") for i, sid in enumerate(ids))


def test_clip_filter_shifts_subtitles_to_the_clip_place_on_the_timeline():
    vf = clip_filter(start_frame=300, frames=90, subs="/w/subs.ass", is_last=False)

    shift = vf.index("setpts=PTS-STARTPTS+300/30/TB")
    assert shift < vf.index("subtitles=/w/subs.ass") < vf.rindex("setpts=PTS-STARTPTS")
    assert "perspective=" in vf and "noise=" in vf
    assert vf.endswith("format=yuv420p")
    assert "pad=" not in vf


def test_only_the_last_clip_gets_the_crt_power_off():
    vf = clip_filter(start_frame=0, frames=90, subs="s.ass", is_last=True)

    assert "pad=1920:1080" in vf and "fade=t=out:st=2.75:d=0.25" in vf


def test_last_clip_too_short_for_the_power_off_is_an_error():
    with pytest.raises(VideoError):
        clip_filter(start_frame=0, frames=20, subs="s.ass", is_last=True)


def test_subtitle_path_is_escaped():
    assert "subtitles=/a\\,b.ass" in clip_filter(0, 90, "/a,b.ass", False)


def test_clip_jobs_split_frames_and_mark_the_last(tmp_path):
    work = WorkDir(tmp_path)

    jobs = clip_jobs(work, _shots("s01", "s02"), (0, 82, 172))

    assert [(j.shot_id, j.start_frame, j.frames, j.is_last) for j in jobs] == [("s01", 0, 82, False), ("s02", 82, 90, True)]
    assert jobs[0].image == work.image("s01") and jobs[1].out == work.clip("s02")


def test_render_clips_bakes_a_still_then_encodes_each_clip(tmp_path):
    work = WorkDir(tmp_path)
    jobs = clip_jobs(work, _shots("s01", "s02"), (0, 82, 172))
    runner = FakeRunner()

    render_clips(jobs, work.subs, VideoConfig(render_workers=2), runner)

    encodes = [c for c in runner.ffmpeg_calls() if ENCODER in c]
    assert len(encodes) == 2
    first = next(c for c in encodes if c[-1] == str(part_path(work.clip("s01"))))
    assert first[first.index("-frames:v") + 1] == "82"
    assert first[first.index("-b:v") + 1] == "4M"
    assert work.clip("s02").exists() and not part_path(work.clip("s02")).exists()


def test_render_clips_surfaces_the_first_failure(tmp_path):
    work = WorkDir(tmp_path)
    jobs = clip_jobs(work, _shots("s01"), (0, 82))

    with pytest.raises(VideoError):
        render_clips(jobs, work.subs, VideoConfig(), FakeRunner(fail_on=ENCODER))


def test_final_mix_copies_video_and_mixes_voice_with_bgm(tmp_path):
    work = WorkDir(tmp_path)
    runner = FakeRunner()
    out = tmp_path / "out.mp4"

    final_mix(work, ("s01", "s02"), 30.0, tmp_path / "bgm.mp3", out, runner)

    cmd = runner.ffmpeg_calls()[-1]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert "volume=-21dB" in graph and "afade=t=out:st=27.000:d=3.0" in graph and "amix=inputs=2" in graph
    assert cmd[-1] == str(part_path(out)) and out.exists()
    listed = work.clip_list.read_text(encoding="utf-8").splitlines()
    assert listed == [f"file '{work.clip('s01')}'", f"file '{work.clip('s02')}'"]


def test_manifest_round_trip_and_image_digest(tmp_path):
    work = WorkDir(tmp_path)
    work.img_dir.mkdir()
    work.image("s01").write_bytes(b"jpg")
    digest = image_digest(work.image("s01"))

    write_manifest(work, VideoConfig(), {"s01": 82}, {"s01": digest})

    manifest = read_manifest(work)
    assert manifest["frames"] == {"s01": 82}
    assert manifest["encoder"] == ENCODER and manifest["bitrate"] == "4M" and manifest["fps"] == 30
    assert len(digest) == 64


def test_read_manifest_missing_or_broken(tmp_path):
    work = WorkDir(tmp_path)
    with pytest.raises(VideoError):
        read_manifest(work)
    work.manifest.write_text("{bad", encoding="utf-8")
    with pytest.raises(VideoError):
        read_manifest(work)
    work.manifest.write_text(json.dumps({"frames": []}), encoding="utf-8")
    with pytest.raises(VideoError):
        read_manifest(work)


def test_failed_clip_keeps_the_previous_good_clip(tmp_path):
    work = WorkDir(tmp_path)
    work.clip_dir.mkdir()
    work.clip("s01").write_bytes(b"good")
    jobs = clip_jobs(work, _shots("s01"), (0, 82))

    with pytest.raises(VideoError):
        render_clips(jobs, work.subs, VideoConfig(), FakeRunner(fail_on=ENCODER))

    assert work.clip("s01").read_bytes() == b"good"


def test_short_video_fade_out_never_starts_before_zero(tmp_path):
    runner = FakeRunner()

    final_mix(WorkDir(tmp_path), ("s01",), 2.0, tmp_path / "bgm.mp3", tmp_path / "o.mp4", runner)

    assert "afade=t=out:st=0.000" in " ".join(runner.ffmpeg_calls()[-1])
