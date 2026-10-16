"""stickman video：init / check / render / reshoot。

每个动作是一个 _cmd_* 函数，通过 set_defaults(handler=...) 挂到子命令上；错误统一由 _run 转成退出码。
"""

import argparse
import sys
from dataclasses import replace
from collections.abc import Callable
from pathlib import Path

from . import pipeline
from .config import VideoConfig, load_config
from .draft import read_draft, write_workdir
from .errors import VideoError
from .lang import LANGS, PRIMARY
from .motion import max_layered
from .shots import load_spec, shot_lines
from .workdir import WorkDir, inside_project, safe_slug

EXIT_OK = 0
EXIT_FAILED = 1
DEFAULT_VIDEOS_DIR = "data/videos"
CHECK_PREVIEW_CHARS = {"zh": 24, "en": 60}  # check 每个镜头预览的旁白长度

Handler = Callable[[argparse.Namespace], int]


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _run(args: argparse.Namespace) -> int:
    try:
        return args.handler(args)
    except (VideoError, OSError) as exc:
        _err(f"错误：{exc}")
        return EXIT_FAILED


def _cmd_init(args: argparse.Namespace) -> int:
    draft_path = Path(args.draft)
    slug = safe_slug(args.slug or draft_path.stem)
    work = WorkDir(inside_project(Path(args.videos_dir) / slug))
    draft = read_draft(draft_path)
    write_workdir(draft, work, args.force)
    for warning in draft.warnings:
        _err(f"注意：{warning}")
    print(f"已写入 {work.root}：{len(draft.lines)} 行旁白，{len(draft.blocks)} 个镜头（bg / scene 待填写）")
    return EXIT_OK


def _cmd_check(args: argparse.Namespace) -> int:
    work = WorkDir(Path(args.work), args.lang)
    spec, lines = load_spec(work)
    for shot, (sid, shot_text) in zip(spec.shots, shot_lines(spec, lines)):
        layered = ""
        if shot.motion:
            names = ", ".join(layer.name for layer in shot.motion.layers) or "整幅图"
            layered = f"  [分层：{shot.motion.kind}（{names}）]"
        text = LANGS[work.lang].joiner.join(shot_text)
        print(f"{sid}  第 {shot.line}–{shot.line + len(shot_text) - 1} 行  {text[:CHECK_PREVIEW_CHARS[work.lang]]}{layered}")
    n_layered = sum(1 for s in spec.shots if s.motion)
    targets = spec.targets(work)
    missing = sum(1 for t in targets if not t.path.is_file())
    print(f"通过：{len(spec.shots)} 个镜头，{len(lines)} 行旁白；"
          f"分层镜头 {n_layered} 个（上限 {max_layered(len(spec.shots))}）；要出图 {len(targets)} 张，缺 {missing} 张")
    return EXIT_OK


def _services() -> pipeline.Services:
    return pipeline.Services()


def _output(value: str | None, work: WorkDir) -> Path:
    """成片只能写在工作目录里、且是 .mp4：ffmpeg 带 -y，写错位置会直接覆盖文件。"""
    if not value:
        return work.default_output()
    out = inside_project(value)
    if out.suffix != ".mp4" or not out.is_relative_to(work.root):
        raise VideoError(f"--out 必须是工作目录 {work.root} 里的 .mp4 文件")
    others = {WorkDir(work.root, code).default_output() for code in LANGS if code != work.lang}
    if out in others:
        raise VideoError(f"--out 不能是其他语言版本的成片：{out.name}")
    return out


def _prepare(args: argparse.Namespace) -> tuple[WorkDir, VideoConfig, Path]:
    work = WorkDir(inside_project(args.work), args.lang)
    safe_slug(work.root.name)  # 目录名会进 ffmpeg 滤镜参数，= 之类的字符会让它解析失败
    speaker_field = LANGS[work.lang].speaker_field  # --speaker 只换当前语言的音色
    config = load_config(work.root, {"ref_image": args.ref, speaker_field: args.speaker, "bgm": args.bgm})
    # 参考图会上传给出图接口，BGM 会被读进成片：都限制在项目内（会解析符号链接）。
    config = replace(config, ref_image=str(inside_project(config.ref_image)), bgm=str(inside_project(config.bgm)))
    return work, config, _output(args.out, work)


def _summary(report: pipeline.RenderReport) -> str:
    media = report.media
    return (f"成片：{report.out}（{report.total:.1f} 秒）；本次出图 {media.images_generated} 张，"
            f"配音计费 {media.billed_audio_s:.1f} 秒")


def _cmd_render(args: argparse.Namespace) -> int:
    work, config, out = _prepare(args)
    print(_summary(pipeline.render(work, config, _services(), out, args.offline)))
    return EXIT_OK


def _cmd_reshoot(args: argparse.Namespace) -> int:
    work, config, out = _prepare(args)
    report = pipeline.reshoot(work, config, _services(), tuple(args.shots), out, generate=not args.no_generate)
    print(_summary(report))
    return EXIT_OK


def _add_lang_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lang", choices=tuple(LANGS), default=PRIMARY,
                        help=f"语言版本，默认 {PRIMARY}；en 读 narration.en.txt，共用中文版的图，出 <目录名>.en.mp4")


def _add_media_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("work", help="工作目录，例如 data/videos/<slug>")
    _add_lang_arg(parser)
    parser.add_argument("--out", help="成片路径，默认 <工作目录>/<目录名>.mp4（英文版 <目录名>.en.mp4）")
    parser.add_argument("--ref", help="角色参考图（默认见 config.py，可在工作目录的 video.json 里覆盖）")
    parser.add_argument("--speaker", help="当前语言的配音音色 id")
    parser.add_argument("--bgm", help="背景音乐")


def build_parser(prog: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="把定稿的文案做成视频")
    sub = parser.add_subparsers(dest="action", required=True)

    init = sub.add_parser("init", help="从草稿生成工作目录：narration.txt + 待填写的 shots.json")
    init.add_argument("draft", help="草稿 .md 文件")
    init.add_argument("--slug", help="工作目录名，默认取草稿文件名")
    init.add_argument("--videos-dir", default=DEFAULT_VIDEOS_DIR, help=f"默认 {DEFAULT_VIDEOS_DIR}")
    init.add_argument("--force", action="store_true", help="覆盖已有的 narration.txt / shots.json")
    init.set_defaults(handler=_cmd_init)

    check = sub.add_parser("check", help="校验 shots.json：镜头 id、行号、bg / scene 是否填好")
    check.add_argument("work", help="工作目录，例如 data/videos/<slug>")
    _add_lang_arg(check)
    check.set_defaults(handler=_cmd_check)

    render = sub.add_parser("render", help="出片：补齐缺的图和配音（付费），渲染并拼接；已有的素材直接复用")
    _add_media_args(render)
    render.add_argument("--offline", action="store_true", help="只用已有的图和配音，不调任何接口")
    render.set_defaults(handler=_cmd_render)

    reshoot = sub.add_parser("reshoot", help="只重做指定镜头：重新出图（付费）、重渲这几段、重新拼接")
    _add_media_args(reshoot)
    reshoot.add_argument("shots", nargs="+", help="镜头 id，例如 s02 s07")
    reshoot.add_argument("--no-generate", action="store_true", help="不重新出图，只用现有的图重渲")
    reshoot.set_defaults(handler=_cmd_reshoot)
    return parser


def main(argv: list[str], prog: str = "stickman video") -> int:
    return _run(build_parser(prog).parse_args(argv))
