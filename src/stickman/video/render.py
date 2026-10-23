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

from .compose import FIT, LOOK, Box, build_graph, measure, portrait_graph
from .config import (
    BGM_FADE_IN,
    BGM_FADE_OUT,
    BGM_GAIN_DB,
    ENCODER,
    FPS,
    LOOK_VERSION,
    VERT_H,
    VERT_TOP,
    VERT_W,
    VOICE_LUFS,
    VOICE_TRUE_PEAK,
    VideoConfig,
)
from .errors import VideoError
from .ffmpeg import Runner, concat_line, ffmpeg, filter_escape
from .motion import Motion
from .shots import Shot
from .thumbnail import HEADLINE_BOX
from .workdir import WorkDir

# 静态的质感先烘进一张 YUV jpg，逐帧只算摆动、颗粒和字幕（比逐帧全算快约 20%）。
# 分层镜头每帧画面都在变，质感只能逐帧算。
STILL_LOOK = f"{FIT},{LOOK}"
GRAIN = "noise=alls=8:allf=t+u"


@dataclass(frozen=True)
class ClipJob:
    shot_id: str
    image: Path
    start_frame: int
    frames: int
    out: Path
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


def clip_filter(start_frame: int, subs: str | Path) -> str:
    return f"{sway_filter()},{_tail(start_frame, subs)}"


def _tail(start_frame: int, subs: str | Path) -> str:
    """摆动或合成之后的共同部分：颗粒、按时间线位置烧字幕。结尾不加动画，停在最后一帧（用户定的）。"""
    return (f"{GRAIN},setpts=PTS-STARTPTS+{start_frame}/{FPS}/TB,"
            f"subtitles={filter_escape(subs)},setpts=PTS-STARTPTS,format=yuv420p")


def vertical_graph(src: str, out: str) -> str:
    """横版画面排进竖屏版画布（裁中间 4:3、上下延伸背景色）。"""
    return portrait_graph(src, out, VERT_W, VERT_H, VERT_TOP)


def _headline_overlay(index: int) -> str:
    """把第 index 路输入（封面字 png）叠到竖屏顶部，中心对准 HEADLINE_BOX 的中心。"""
    box = HEADLINE_BOX
    return f"[{index}:v]format=rgba[hl];[frame][hl]overlay=x={box.x + box.w // 2}-w/2:y={box.y + box.h // 2}-h/2"


def clip_jobs(work: WorkDir, shots: tuple[Shot, ...], frames: tuple[int, ...]) -> tuple[ClipJob, ...]:
    return tuple(
        ClipJob(s.id, work.image(s.id), f0, f1 - f0, work.clip(s.id), s.motion,
                tuple((layer.name, work.layer(s.id, layer.name)) for layer in s.motion.layers) if s.motion else ())
        for s, f0, f1 in zip(shots, frames, frames[1:])
    )


def part_path(path: Path) -> Path:
    """先写到 x.part.mp4，成功后再改名：中途失败不会留下半截文件被当成好的拼进成片。"""
    return path.with_name(f"{path.stem}.part{path.suffix}")


def _render_layered(job: ClipJob, subs: Path, config: VideoConfig, runner: Runner, headline: Path | None) -> None:
    layers = dict(job.layers)

    def probe(name: str, key: tuple[int, int, int]) -> Box:
        return measure(layers[name], key, job.out.with_name(f"{job.shot_id}.{name}.ppm"), runner)

    dur = job.frames / FPS
    graph = build_graph(job.motion, layers, job.image, dur, probe)
    inputs = [arg for path in graph.inputs
              for arg in ("-f", "image2", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", path)]
    if headline is None:
        full = f"{graph.graph};[comp]{LOOK},{_tail(job.start_frame, subs)}[v]"
    else:
        inputs += ["-i", headline]
        full = (f"{graph.graph};{vertical_graph('comp', 'vert')};[vert]{LOOK}[frame];"
                f"{_headline_overlay(len(graph.inputs))},{_tail(job.start_frame, subs)}[v]")
    part = part_path(job.out)
    ffmpeg([*inputs, "-filter_complex", full, "-map", "[v]", "-frames:v", str(job.frames),
            "-c:v", ENCODER, "-b:v", config.video_bitrate, part], runner)
    os.replace(part, job.out)


def render_clip(job: ClipJob, subs: Path, config: VideoConfig, runner: Runner, headline: Path | None = None) -> None:
    """headline 是竖屏版顶部的封面字；不给就是横版。"""
    if job.motion is not None:
        _render_layered(job, subs, config, runner, headline)
        return
    still, part = job.out.with_suffix(".still.jpg"), part_path(job.out)
    # -f image2：图片是从网上下载的，不让 ffmpeg 按内容去猜别的格式。
    look = (["-vf", STILL_LOOK] if headline is None
            else ["-filter_complex", f"[0:v]{FIT}[fit];{vertical_graph('fit', 'vert')};[vert]{LOOK}"])
    ffmpeg(["-f", "image2", "-i", job.image, *look, "-frames:v", "1", "-q:v", "2", "-pix_fmt", "yuvj420p", still], runner)
    if headline is None:
        video = ["-vf", clip_filter(job.start_frame, subs)]
    else:
        video = ["-i", headline, "-filter_complex",
                 f"[0:v]{sway_filter()}[frame];{_headline_overlay(1)},{_tail(job.start_frame, subs)}"]
    ffmpeg(["-loop", "1", "-framerate", str(FPS), "-i", still, *video, "-frames:v", str(job.frames),
            "-c:v", ENCODER, "-b:v", config.video_bitrate, part], runner)
    os.replace(part, job.out)


def render_clips(jobs: tuple[ClipJob, ...], subs: Path, config: VideoConfig, runner: Runner,
                 headline: Path | None = None) -> None:
    if jobs:
        jobs[0].out.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(config.render_workers) as pool:
        futures = [pool.submit(render_clip, job, subs, config, runner, headline) for job in jobs]
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


def shot_looks(shots: tuple[Shot, ...], images: dict[str, str]) -> dict[str, str]:
    """每个镜头画面的指纹：用了哪几张图、什么运动参数、哪一版画面效果。同一镜头中英文两版的指纹必须相同。"""
    looks = {}
    for shot in shots:
        used = sorted((k, v) for k, v in images.items() if k.partition(":")[0] == shot.id)
        motion = None if shot.motion is None else (shot.motion.kind, shot.motion.params)
        looks[shot.id] = hashlib.sha256(repr((LOOK_VERSION, motion, used)).encode()).hexdigest()
    return looks


def write_manifest(work: WorkDir, config: VideoConfig, frames: dict[str, int], images: dict[str, str],
                   looks: dict[str, str], headline: str | None = None) -> None:
    """headline 是竖屏版顶部封面字的指纹，reshoot 用它确认新旧片段顶部的字一样。"""
    manifest = {
        "fps": FPS, "encoder": ENCODER, "bitrate": config.video_bitrate,
        "total_frames": sum(frames.values()), "frames": frames, "images": images, "looks": looks,
        **({} if headline is None else {"headline": headline}),
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
