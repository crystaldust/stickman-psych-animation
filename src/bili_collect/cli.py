"""命令行入口：bili-collect list / fetch / collect。"""

import argparse
import logging
import sys
from functools import partial
from pathlib import Path

from .bili import AccessDeniedError, BiliClient, BiliError, ClientOptions
from .media import MediaError, extract_audio
from .models import VideoEntry
from .pipeline import Context, collect_user, list_user, process_video
from .storage import Layout
from .targets import Target, TargetError, parse_target
from .transcribe import DEFAULT_MODEL, transcribe_audio

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130
NOISY_LOGGERS = ("httpx", "httpcore", "huggingface_hub", "urllib3")
COOKIE_HINT = "提示：加上 --cookies-from-browser chrome（或 safari / firefox / edge），使用已登录 B 站的浏览器 cookies"
MLX_HINT = "转写需要 Apple Silicon 上的 mlx-whisper，请确认在 M 系列 Mac 上运行并执行过 uv sync"


def _positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def _non_negative_float(value: str) -> float:
    number = float(value)
    if number < 0:
        raise argparse.ArgumentTypeError("不能为负数")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bili-collect", description="B 站 up 主内容采集工具")
    parser.add_argument("--data-dir", default="data", help="数据目录（默认 ./data）")
    cookies = parser.add_mutually_exclusive_group()
    cookies.add_argument("--cookies-from-browser", metavar="BROWSER", help="从浏览器读取 B 站 cookies，如 chrome")
    cookies.add_argument("--cookies", type=Path, metavar="FILE", help="Netscape 格式的 cookies 文件（只读使用）")
    parser.add_argument("--sleep", type=_non_negative_float, default=1.0, help="请求间隔秒数（默认 1.0）")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Whisper 模型（Hugging Face repo）")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出 up 主的视频并写入索引")
    p_list.add_argument("target", help="mid 或空间链接")
    p_list.add_argument("--limit", type=_positive_int, help="最多列出多少条（从最新开始）")

    p_fetch = sub.add_parser("fetch", help="处理单个视频")
    p_fetch.add_argument("target", help="BV 号或视频链接")
    p_fetch.add_argument("--video", action="store_true", help="下载视频文件")
    p_fetch.add_argument("--audio", action="store_true", help="抽取音频为 wav")
    p_fetch.add_argument("--transcribe", action="store_true", help="转写成带时间戳的文案（含抽音频）")

    p_collect = sub.add_parser("collect", help="批量处理 up 主的视频，已完成的会跳过")
    p_collect.add_argument("target", help="mid 或空间链接")
    p_collect.add_argument("--limit", type=_positive_int, help="最多处理多少条（从最新开始）")
    p_collect.add_argument("--video", action="store_true", help="同时下载视频文件")
    p_collect.add_argument("--transcribe", action="store_true", help="转写成带时间戳的文案")
    return parser


def build_context(args: argparse.Namespace) -> Context:
    options = ClientOptions(
        cookies_from_browser=args.cookies_from_browser,
        cookie_file=args.cookies,
        sleep_seconds=args.sleep,
    )
    return Context(
        client=BiliClient(options),
        layout=Layout(Path(args.data_dir)),
        extract_audio=extract_audio,
        transcribe_audio=partial(transcribe_audio, model=args.model),
    )


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _format_entry(entry: VideoEntry) -> str:
    date = entry.upload_date or "--------"
    minutes, secs = divmod(int(entry.duration or 0), 60)
    return f"{entry.bvid}  {date}  {minutes:02d}:{secs:02d}  {entry.title}"


def _resolve(args: argparse.Namespace) -> Target:
    target = parse_target(args.target)
    expected = "video" if args.command == "fetch" else "user"
    if target.kind != expected:
        raise TargetError("fetch 需要视频 BV 号或链接" if expected == "video" else "需要 up 主的 mid 或空间链接")
    return target


def _run_list(args: argparse.Namespace, target: Target) -> int:
    for entry in list_user(build_context(args), target.id, limit=args.limit):
        print(_format_entry(entry))
    return EXIT_OK


def _run_fetch(args: argparse.Namespace, target: Target) -> int:
    result = process_video(
        build_context(args), target.id, video=args.video, audio=args.audio, transcribe=args.transcribe
    )
    print(f"保存到：{result.directory}")
    if result.transcript:
        print(f"文案：{result.transcript}")
    return EXIT_OK


def _run_collect(args: argparse.Namespace, target: Target) -> int:
    report = collect_user(
        build_context(args), target.id, limit=args.limit, video=args.video, transcribe=args.transcribe
    )
    print(f"完成 {len(report.done)}，跳过 {len(report.skipped)}，失败 {len(report.failed)}")
    for failure in report.failed:
        _err(f"失败 {failure.bvid}：{failure.reason}")
    return EXIT_FAILED if report.failed else EXIT_OK


COMMANDS = {"list": _run_list, "fetch": _run_fetch, "collect": _run_collect}


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    try:
        target = _resolve(args)
    except TargetError as exc:
        _err(f"错误：{exc}")
        return EXIT_USAGE

    try:
        return COMMANDS[args.command](args, target)
    except KeyboardInterrupt:
        _err("已中断，已完成的部分会在下次运行时跳过")
        return EXIT_INTERRUPTED
    except AccessDeniedError as exc:
        _err(f"错误：{exc}\n{COOKIE_HINT}")
    except ModuleNotFoundError as exc:
        _err(f"错误：{exc}\n{MLX_HINT}")
    except (BiliError, MediaError, OSError, ValueError) as exc:
        _err(f"错误：{exc}")
    return EXIT_FAILED


def run() -> None:
    sys.exit(main())
