"""镜头片段与成片：每个镜头一段（整张图缓慢透视摆动，或分层合成 + 胶片质感 + 烧字幕），再无损拼接、混入配音和 BGM。

字幕按片段在时间线上的位置烧进每段，所以单独重渲一段时帧数必须不变；
拼接用 -c copy，所以所有片段必须用同样的编码参数——两者都记在 render.json 里核对。
"""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .compose import FIT, LOOK, Box, build_graph, measure
from .config import (
    BGM_FADE_IN,
    BGM_FADE_OUT,
    BGM_GAIN_DB,
    ENCODER,
    FPS,
    FRAME_H,
    FRAME_W,
    OFF_DUR,
    VOICE_LUFS,
    VOICE_TRUE_PEAK,
    VideoConfig,
)
from .errors import VideoError
from .ffmpeg import Runner, concat_line, ffmpeg, filter_escape
from .motion import Motion
from .shots import Shot
from .workdir import WorkDir

# 静态的质感先烘进一张 YUV jpg，逐帧只算摆动、颗粒和字幕（比逐帧全算快约 20%）。
# 分层镜头每帧画面都在变，质感只能逐帧算。
STILL_LOOK = f"{FIT},{LOOK}"
GRAIN = "noise=alls=8:allf=t+u"
FADE_TAIL = 0.25


@dataclass(frozen=True)
class ClipJob:
    shot_id: str
    image: Path
    start_frame: int
    frames: int
    out: Path
    is_last: bool
    motion: Motion | None = None
    layers: tuple[tuple[str, Path], ...] = ()  # 分层镜头的（图层名，图片）


def sway_filter() -> str:
    """缓慢、轻微的透视摆动（四个角各自按不同周期正弦漂移），不逐帧抖。"""
    def a(period: float) -> str:
        return f"6*sin(2*PI*on/{FPS}/{period})"
    return (
        f"perspective=x0='{a(3.1)}':y0='{a(2.7)}':x1='W+{a(2.9)}':y1='{a(3.4)}'"
        f":x2='{a(3.3)}':y2='H+{a(2.6)}':x3='W+{a(2.8)}':y3='H+{a(3.2)}'"
        ":interpolation=linear:eval=frame"
    )


def clip_filter(start_frame: int, frames: int, subs: str | Path, is_last: bool) -> str:
    return f"{sway_filter()},{_tail(start_frame, frames, subs, is_last)}"


def _tail(start_frame: int, frames: int, subs: str | Path, is_last: bool) -> str:
    """摆动或合成之后的共同部分：颗粒、按时间线位置烧字幕、最后一镜的 CRT 关机。"""
    vf = (f"{GRAIN},setpts=PTS-STARTPTS+{start_frame}/{FPS}/TB,"
          f"subtitles={filter_escape(subs)},setpts=PTS-STARTPTS")
    if is_last:
        dur = frames / FPS
        if dur < OFF_DUR + FADE_TAIL:
            raise VideoError(f"最后一个镜头只有 {dur:.2f} 秒，放不下 CRT 关机动画")
        off = dur - OFF_DUR
        vf += (f",scale=w=iw:h='if(gte(t,{off:.2f}),max(4,ih*(1-(t-{off:.2f})/{OFF_DUR * 0.6:.2f})),ih)':eval=frame,"
               f"pad={FRAME_W}:{FRAME_H}:(ow-iw)/2:(oh-ih)/2:black,fade=t=out:st={dur - FADE_TAIL:.2f}:d={FADE_TAIL}")
    return vf + ",format=yuv420p"


def clip_jobs(work: WorkDir, shots: tuple[Shot, ...], frames: tuple[int, ...]) -> tuple[ClipJob, ...]:
    return tuple(
        ClipJob(s.id, work.image(s.id), f0, f1 - f0, work.clip(s.id), i == len(shots) - 1, s.motion,
                tuple((layer.name, work.layer(s.id, layer.name)) for layer in s.motion.layers) if s.motion else ())
        for i, (s, f0, f1) in enumerate(zip(shots, frames, frames[1:]))
    )


def part_path(path: Path) -> Path:
    """先写到 x.part.mp4，成功后再改名：中途失败不会留下半截文件被当成好的拼进成片。"""
    return path.with_name(f"{path.stem}.part{path.suffix}")


def _render_layered(job: ClipJob, subs: Path, config: VideoConfig, runner: Runner) -> None:
    layers = dict(job.layers)

    def probe(name: str, key: tuple[int, int, int]) -> Box:
        return measure(layers[name], key, job.out.with_name(f"{job.shot_id}.{name}.ppm"), runner)

    dur = job.frames / FPS
    graph = build_graph(job.motion, layers, job.image, dur, probe)
    inputs = [arg for path in graph.inputs
              for arg in ("-f", "image2", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", path)]
    full = f"{graph.graph};[comp]{LOOK},{_tail(job.start_frame, job.frames, subs, job.is_last)}[v]"
    part = part_path(job.out)
    ffmpeg([*inputs, "-filter_complex", full, "-map", "[v]", "-frames:v", str(job.frames),
            "-c:v", ENCODER, "-b:v", config.video_bitrate, part], runner)
    os.replace(part, job.out)


def render_clip(job: ClipJob, subs: Path, config: VideoConfig, runner: Runner) -> None:
    if job.motion is not None:
        _render_layered(job, subs, config, runner)
        return
    still, part = job.out.with_suffix(".still.jpg"), part_path(job.out)
    # -f image2：图片是从网上下载的，不让 ffmpeg 按内容去猜别的格式。
    ffmpeg(["-f", "image2", "-i", job.image, "-vf", STILL_LOOK, "-frames:v", "1", "-q:v", "2",
            "-pix_fmt", "yuvj420p", still], runner)
    ffmpeg(["-loop", "1", "-framerate", str(FPS), "-i", still, "-frames:v", str(job.frames),
            "-vf", clip_filter(job.start_frame, job.frames, subs, job.is_last),
            "-c:v", ENCODER, "-b:v", config.video_bitrate, part], runner)
    os.replace(part, job.out)


def render_clips(jobs: tuple[ClipJob, ...], subs: Path, config: VideoConfig, runner: Runner) -> None:
    if jobs:
        jobs[0].out.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(config.render_workers) as pool:
        futures = [pool.submit(render_clip, job, subs, config, runner) for job in jobs]
        for future in futures:
            future.result()


def final_mix(work: WorkDir, ids: tuple[str, ...], total: float, bgm: Path, out: Path, runner: Runner) -> None:
    work.clip_list.write_text("".join(concat_line(work.clip(sid)) for sid in ids), encoding="utf-8")
    music = (
        f"[2:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,volume={BGM_GAIN_DB}dB,"
        f"afade=t=in:st=0:d={BGM_FADE_IN},afade=t=out:st={max(0.0, total - BGM_FADE_OUT):.3f}:d={BGM_FADE_OUT}[m]"
    )
    mix = (f"[1:a]loudnorm=I={VOICE_LUFS}:TP={VOICE_TRUE_PEAK},aresample=44100,"  # loudnorm 默认输出 192k 采样率
           "aformat=channel_layouts=stereo[vo];"
           "[vo][m]amix=inputs=2:duration=first:normalize=0[a]")
    ffmpeg(["-f", "concat", "-safe", "0", "-i", work.clip_list, "-i", work.voice_wav, "-i", bgm,
            "-filter_complex", f"{music};{mix}", "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", part_path(out)], runner)
    os.replace(part_path(out), out)


def image_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(work: WorkDir, config: VideoConfig, frames: dict[str, int], images: dict[str, str]) -> None:
    manifest = {
        "fps": FPS, "encoder": ENCODER, "bitrate": config.video_bitrate,
        "total_frames": sum(frames.values()), "frames": frames, "images": images,
    }
    work.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def read_manifest(work: WorkDir) -> dict:
    try:
        manifest = json.loads(work.manifest.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VideoError(f"还没有完整渲染过（缺 {work.manifest}），先运行 render") from exc
    except json.JSONDecodeError as exc:
        raise VideoError(f"{work.manifest} 不是合法的 JSON：{exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("frames"), dict):
        raise VideoError(f"{work.manifest} 格式不对，重新运行 render")
    return manifest
