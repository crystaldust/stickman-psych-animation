"""整条流水线：分镜 → 出图 ∥ 配音 → 拼配音、算字幕 → 渲染片段 → 拼接混音。

WorkDir.lang 选语言版本。图和分镜归中文版（主版本）：英文版只配音、渲染，不出图，缺图时报错。
WorkDir.fmt 选画幅。竖屏版（9x16）共用同一语言横版的配音，不调任何接口，只重新排画面、加顶部封面字。

已有的图和配音直接复用，所以失败后重跑只会补缺的那几个。配音只要和旁白对不上就报错，
不会悄悄重新生成——那是要计费的。
"""

import json
import os
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import images, publish, tts
from .audio import Segment, VoiceTimeline, build_voice, missing_voice, read_meta
from .config import ENCODER, FPS, FRAME_H, FRAME_W, KEYCHAIN_IMAGE, KEYCHAIN_TTS, VERT_H, VERT_W, VideoConfig
from .errors import VideoError
from .ffmpeg import Runner, count_frames, run_command
from .keychain import read_secret
from .lang import LANGS, PRIMARY, Lang
from .render import (
    ClipJob,
    clip_jobs,
    final_mix,
    image_digest,
    read_manifest,
    render_clips,
    shot_looks,
    write_manifest,
)
from .shots import ImageTarget, ShotSpec, load_spec, shot_lines
from .subtitles import ass_document, srt_document
from .timeline import clip_frames
from .workdir import WIDE, WorkDir, safe_id


@dataclass(frozen=True)
class Services:
    """外部依赖，测试时整组替换。"""
    runner: Runner = run_command
    read_secret: Callable[[str], str] = read_secret
    synthesize: Callable[..., tts.TtsResult] = tts.synthesize
    generate_image: Callable[..., None] = images.generate_image


@dataclass(frozen=True)
class MediaReport:
    images_generated: int = 0
    billed_audio_s: float = 0.0


@dataclass(frozen=True)
class MediaInputs:
    """调接口前要准备好的东西：参考图和 key。先准备好，再动任何文件。"""
    ref_url: str = ""
    img_key: str = ""
    tts_key: str = ""


@dataclass(frozen=True)
class RenderReport:
    out: Path
    total: float
    media: MediaReport
    timings: dict[str, float]


@contextmanager
def _stage(timings: dict[str, float], name: str) -> Iterator[None]:
    print(f"[{name}]", flush=True)
    start = time.monotonic()
    yield
    timings[name] = round(time.monotonic() - start, 1)
    print(f"[{name}] {timings[name]}s", flush=True)


def _existing(value: str, kind: str) -> Path:
    path = Path(value)
    if not path.is_file():
        raise VideoError(f"找不到{kind}：{path}")
    return path


def _lang(work: WorkDir) -> Lang:
    return LANGS[work.lang]


def _wide(work: WorkDir) -> bool:
    return work.fmt == WIDE


def _follows(work: WorkDir) -> bool:
    """画面跟着中文横版走、自己不出图的版本：其他语言版和竖屏版。"""
    return work.lang != PRIMARY or not _wide(work)


def _stale_voices(work: WorkDir, segments: tuple[Segment, ...]) -> tuple[str, ...]:
    """已有配音里，生成时的旁白和现在不一样的镜头。

    旧流水线生成的配音没有 source_text，这里放行；之后 char_times 逐字比对内容，对不上同样会报错。
    """
    cached = set(s for s, _ in segments) - set(missing_voice(work, tuple(s for s, _ in segments)))
    joiner = _lang(work).joiner
    return tuple(
        sid for sid, lines in segments
        if sid in cached and read_meta(work, sid).get("source_text") not in (None, joiner.join(lines))
    )


def _voice_job(services: Services, text: str, key: str, config: VideoConfig, work: WorkDir, sid: str) -> float:
    result = services.synthesize(text, key, config, _lang(work), label=f"配音 {sid}")
    tts.save_voice(result, work.voice(sid), work.voice_meta(sid))
    return result.billed_s


def _collect(jobs: dict[Future, tuple[str, str]], t0: float) -> MediaReport:
    made, billed, errors = 0, 0.0, []
    for future in as_completed(jobs):
        kind, sid = jobs[future]
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 - 每个任务的失败都要汇总报告，不能让一个异常吞掉其余结果
            errors.append(str(exc) if isinstance(exc, (VideoError, OSError)) else f"{kind} {sid}：{type(exc).__name__}: {exc}")
            print(f"  失败 {exc}", flush=True)
            continue
        made, billed = (made + 1, billed) if kind == "img" else (made, billed + result)
        print(f"  {kind} {sid} 完成（t+{time.monotonic() - t0:.0f}s）", flush=True)
    if errors:
        raise VideoError(f"{len(errors)} 个任务失败（成功的已保存，重跑只补缺的）：\n  " + "\n  ".join(errors))
    return MediaReport(made, billed)


def media_inputs(config: VideoConfig, services: Services, need_img: bool, need_tts: bool) -> MediaInputs:
    ref_url = images.ref_data_url(Path(config.ref_image)) if need_img else ""
    return MediaInputs(ref_url,
                       services.read_secret(KEYCHAIN_IMAGE) if need_img else "",
                       services.read_secret(KEYCHAIN_TTS) if need_tts else "")


def media_stage(work: WorkDir, segments: tuple[Segment, ...], config: VideoConfig, services: Services,
                targets: tuple[ImageTarget, ...], inputs: MediaInputs | None = None) -> MediaReport:
    """补齐缺的图（限 targets 内）和缺的配音；key 只在确实要调接口时读，且只在主线程读一次。"""
    stale = _stale_voices(work, segments)
    if stale:
        raise VideoError(f"这些镜头的配音和现在的旁白对不上：{', '.join(stale)}。"
                         f"确认要重新配音（会计费）就删掉对应的 {work.voice_dir.name}/<id>.mp3 再运行")
    need_img = tuple(t for t in targets if not t.path.is_file())
    no_voice = set(missing_voice(work, tuple(s for s, _ in segments)))
    need_tts = tuple((sid, _lang(work).joiner.join(lines)) for sid, lines in segments if sid in no_voice)
    if not need_img and not need_tts:
        return MediaReport()
    inputs = inputs or media_inputs(config, services, bool(need_img), bool(need_tts))
    for target in need_img:
        target.path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    pools = (ThreadPoolExecutor(config.img_workers), ThreadPoolExecutor(config.tts_workers))
    try:
        jobs = {pools[0].submit(services.generate_image, t.prompt, inputs.ref_url, inputs.img_key, config,
                                t.path, label=f"出图 {t.id}"): ("img", t.id) for t in need_img}
        jobs |= {pools[1].submit(_voice_job, services, text, inputs.tts_key, config, work, sid): ("tts", sid)
                 for sid, text in need_tts}
        return _collect(jobs, t0)
    except KeyboardInterrupt:
        # Ctrl-C：取消还没开始的付费任务，只等正在进行的那几个结束。
        for pool in pools:
            pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        for pool in pools:
            pool.shutdown()


def _require_cached(work: WorkDir, spec: ShotSpec, segments: tuple[Segment, ...],
                    skip_images: tuple[str, ...] = ()) -> MediaReport:
    """不调接口时：缺什么一次列出来（skip_images 是马上要重新出的图的 id）。"""
    stale = _stale_voices(work, segments)
    no_img = [t.id for t in spec.targets(work) if t.id not in skip_images and not t.path.is_file()]
    no_voice = missing_voice(work, spec.ids())
    if stale or no_img or no_voice:
        fix = "先运行 render 补齐" if _wide(work) else "竖屏版不调接口，先把横版 render 完整"
        raise VideoError(f"素材不全：缺图 {no_img or '无'}；缺配音 {list(no_voice) or '无'}；"
                         f"配音过期 {list(stale) or '无'}。{fix}")
    return MediaReport()


def _voice(work: WorkDir, segments: tuple[Segment, ...], runner: Runner) -> VoiceTimeline:
    lang = _lang(work)
    timeline = build_voice(work, segments, runner)
    style, size = (lang.sub_style, (FRAME_W, FRAME_H)) if _wide(work) else (lang.sub_style_9x16, (VERT_W, VERT_H))
    work.subs.write_text(ass_document(timeline.events, style, size), encoding="utf-8")
    work.srt.write_text(srt_document(timeline.events), encoding="utf-8")
    return timeline


def _frame_map(ids: tuple[str, ...], frames: tuple[int, ...]) -> dict[str, int]:
    return {sid: b - a for sid, a, b in zip(ids, frames, frames[1:])}


def _digests(targets: tuple[ImageTarget, ...]) -> dict[str, str]:
    return {t.id: image_digest(t.path) for t in targets}


def _follow_primary(work: WorkDir, spec: ShotSpec, targets: tuple[ImageTarget, ...]) -> dict[str, str]:
    """其他语言版和竖屏版的画面跟着中文版成片走：中文版每个镜头都要是用现在的图、motion 和画面效果渲染的。

    在配音、渲染之前检查，不一致就不花钱。返回现在每个镜头的画面指纹。
    """
    missing = [t.id for t in targets if not t.path.is_file()]
    if missing:
        raise VideoError(f"{work.label}共用中文版的图，缺图 {', '.join(missing)}：先渲染中文版")
    primary = WorkDir(work.root)
    if not primary.manifest.is_file():
        raise VideoError(f"{work.label}的画面跟着中文版走，中文版还没有用 render 渲染过：先渲染中文版")
    looks = shot_looks(spec.shots, _digests(targets))
    done = read_manifest(primary).get("looks", {})
    stale = [sid for sid, look in looks.items() if done.get(sid) != look]
    if stale:
        ids = " ".join(stale)
        raise VideoError(f"中文版成片里这些镜头不是用现在的图、motion 或画面效果渲染的：{ids}。"
                         f"先重渲中文版（reshoot <目录> {ids} --no-generate），再渲染{work.label}")
    return looks


def _headline(work: WorkDir, spec: ShotSpec, config: VideoConfig, runner: Runner) -> publish.Headline | None:
    """竖屏版顶部的封面字（每次渲染都按 publish.json 重画）；横版没有。"""
    return None if _wide(work) else publish.headline(work, spec, Path(config.thumb_font), runner)


def render(work: WorkDir, config: VideoConfig, services: Services, out: Path, offline: bool) -> RenderReport:
    spec, lines = load_spec(work)
    segments, ids = shot_lines(spec, lines), spec.ids()
    bgm = _existing(config.bgm, " BGM")
    timings: dict[str, float] = {}
    t_all = time.monotonic()
    targets = spec.targets(work)
    if _follows(work):
        _follow_primary(work, spec, targets)
    to_generate = () if _follows(work) else targets  # 其他语言版、竖屏版共用中文版的图，不自己出图
    with _stage(timings, "media"):
        media = (_require_cached(work, spec, segments) if offline or not _wide(work)
                 else media_stage(work, segments, config, services, to_generate))
    headline = _headline(work, spec, config, services.runner)
    with _stage(timings, "voice"):
        timeline = _voice(work, segments, services.runner)
    frames = clip_frames(timeline.shot_starts, timeline.total)
    with _stage(timings, "clips"):
        render_clips(clip_jobs(work, spec.shots, frames), work.subs, config, services.runner,
                     headline and headline.path)
    images = _digests(targets)
    write_manifest(work, config, _frame_map(ids, frames), images, shot_looks(spec.shots, images),
                   headline and headline.fingerprint)
    with _stage(timings, "final"):
        out.parent.mkdir(parents=True, exist_ok=True)
        final_mix(work, ids, timeline.total, bgm, out, services.runner)
    timings["total"] = round(time.monotonic() - t_all, 1)
    report = {"timings_s": timings, "video_s": round(timeline.total, 1),
              "images_generated": media.images_generated, "billed_audio_s": round(media.billed_audio_s, 1)}
    work.timings.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return RenderReport(out, timeline.total, media, timings)


def _selected(spec: ShotSpec, work: WorkDir, selectors: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[ImageTarget, ...]]:
    """reshoot 的参数：s03 是整个镜头（分层镜头即它的所有图层），s03:fg 是其中一层。返回（镜头 id，要重出的图）。"""
    targets = spec.targets(work)
    chosen: dict[str, ImageTarget] = {}
    unknown = []
    for sel in selectors:
        sid = sel.partition(":")[0]
        safe_id(sid)
        match = [t for t in targets if (t.id == sel if ":" in sel else t.shot_id == sid)]
        if not match:
            unknown.append(sel)
        chosen |= {t.id: t for t in match}
    if unknown:
        raise VideoError(f"shots.json 里没有这些镜头或图层：{', '.join(unknown)}")
    picked = tuple(t for t in targets if t.id in chosen)
    return tuple(dict.fromkeys(t.shot_id for t in picked)), picked


def _check_encoding(manifest: dict, config: VideoConfig) -> None:
    current = {"fps": FPS, "encoder": ENCODER, "bitrate": config.video_bitrate}
    changed = [k for k, v in current.items() if manifest.get(k) != v]
    if changed:
        raise VideoError(f"编码参数和上次渲染不同（{', '.join(changed)}），片段没法无损拼接，请运行 render")


def _reject(work: WorkDir, target: ImageTarget, rendered_digest: str | None) -> None:
    """上次成片用的图移到 img_rejected/<id>.<n>.jpg（图层是 <镜头>-<图层>.<n>.jpg），不删。

    和上次成片不是同一张的（上一次 reshoot 中途失败前已经生成好的新图）不动，免得重跑时再付一次钱。
    """
    image, stem = target.path, target.rejected_stem
    if not image.is_file() or (rendered_digest is not None and image_digest(image) != rendered_digest):
        return
    work.rejected_dir.mkdir(parents=True, exist_ok=True)
    n = 1
    while (work.rejected_dir / f"{stem}.{n}.jpg").exists():
        n += 1
    os.replace(image, work.rejected_dir / f"{stem}.{n}.jpg")


def _check_new_clips(jobs: tuple[ClipJob, ...], runner: Runner) -> None:
    for job in jobs:
        n = count_frames(job.out, runner)
        if n != job.frames:
            raise VideoError(f"{job.shot_id}：新片段有 {n} 帧，应该是 {job.frames} 帧，请运行 render")


def _timeline_unchanged(work: WorkDir, manifest: dict, all_ids: tuple[str, ...],
                        segments: tuple[Segment, ...], runner: Runner) -> tuple[VoiceTimeline, tuple[int, ...]]:
    timeline = _voice(work, segments, runner)
    frames = clip_frames(timeline.shot_starts, timeline.total)
    moved = [sid for sid, n in _frame_map(all_ids, frames).items() if manifest["frames"].get(sid) != n]
    if moved:
        raise VideoError(f"时间线和上次渲染不同（{', '.join(moved)}），不能只重渲部分镜头，请运行 render")
    return timeline, frames


def reshoot(work: WorkDir, config: VideoConfig, services: Services, shot_ids: tuple[str, ...],
            out: Path, generate: bool) -> RenderReport:
    """只重做指定镜头或图层：（可选）重新出图 → 重渲这些片段 → 重新拼接混音。时间线和编码参数必须和上次一致。"""
    if generate and _follows(work):
        raise VideoError(f"{work.label}共用中文版的图，不能在这里重新出图：先在中文版 reshoot 出新图，"
                         f"再对{work.label}运行 reshoot --no-generate")
    spec, lines = load_spec(work)
    (ids, targets), all_ids, segments = _selected(spec, work, shot_ids), spec.ids(), shot_lines(spec, lines)
    manifest = read_manifest(work)
    _check_encoding(manifest, config)
    bgm = _existing(config.bgm, " BGM")
    _require_cached(work, spec, segments, skip_images=tuple(t.id for t in targets) if generate else ())
    if _follows(work):
        looks = _follow_primary(work, spec, spec.targets(work))
        done = manifest.get("looks", {})
        behind = [sid for sid in all_ids if sid not in ids and done.get(sid) != looks[sid]]
        if behind:
            raise VideoError(f"{work.label}这些镜头和中文版成片的画面不一致，要一起重渲：{' '.join(behind)}")
    headline = _headline(work, spec, config, services.runner)
    if headline is not None and manifest.get("headline") != headline.fingerprint:
        raise VideoError("封面字（或封面镜头的图、字体）和上次竖屏渲染时不同，只重渲几个镜头会让顶部的字前后不一样，"
                         "请运行 render")
    timings: dict[str, float] = {}
    with _stage(timings, "voice"):
        timeline, frames = _timeline_unchanged(work, manifest, all_ids, segments, services.runner)
    media = MediaReport()
    if generate:
        inputs = media_inputs(config, services, need_img=True, need_tts=False)  # 参考图、key 有问题就在动文件之前失败
        for target in targets:
            _reject(work, target, manifest.get("images", {}).get(target.id))
        with _stage(timings, "media"):
            media = media_stage(work, segments, config, services, targets, inputs)
    jobs = tuple(j for j in clip_jobs(work, spec.shots, frames) if j.shot_id in ids)
    with _stage(timings, "clips"):
        render_clips(jobs, work.subs, config, services.runner, headline and headline.path)
        _check_new_clips(jobs, services.runner)
    current = {t.id for t in spec.targets(work)}  # 清单里 shots.json 已经没有的图（比如改分层前的整图）不再带下去
    digests = {k: v for k, v in {**manifest.get("images", {}), **_digests(targets)}.items() if k in current}
    redone = {sid: look for sid, look in shot_looks(spec.shots, digests).items() if sid in ids}
    write_manifest(work, config, _frame_map(all_ids, frames), digests, {**manifest.get("looks", {}), **redone},
                   headline and headline.fingerprint)
    with _stage(timings, "final"):
        out.parent.mkdir(parents=True, exist_ok=True)
        final_mix(work, all_ids, timeline.total, bgm, out, services.runner)
    return RenderReport(out, timeline.total, media, timings)
