"""统一入口：stickman <子命令> ...，各子命令保持自己的参数解析。"""

import argparse
import sys
from collections.abc import Callable

from .collect.bilibili import cli as bili_cli
from .writing import cli as writing_cli

Handler = Callable[[list[str]], int]


def _run_bili(argv: list[str]) -> int:
    return bili_cli.main(argv, prog="stickman bili")


COMMANDS: dict[str, tuple[str, Handler]] = {
    "bili": ("B 站采集：list / fetch / collect（同 bili-collect）", _run_bili),
    "corpus": ("读取已采集的文案：show / list / export", lambda argv: writing_cli.corpus_main(argv)),
    "analysis": ("文案分析结果：save / show / list", lambda argv: writing_cli.analysis_main(argv)),
    "library": ("出处库：build / show", lambda argv: writing_cli.library_main(argv)),
    "draft": ("草稿查重：check", lambda argv: writing_cli.draft_main(argv)),
}


def build_parser() -> argparse.ArgumentParser:
    lines = "\n".join(f"  {name:<8}{summary}" for name, (summary, _) in COMMANDS.items())
    parser = argparse.ArgumentParser(
        prog="stickman",
        description="火柴人频道内容工具",
        epilog=f"子命令：\n{lines}\n\n查看子命令用法：stickman <子命令> --help",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=sorted(COMMANDS), metavar="子命令")
    parser.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    _, handler = COMMANDS[args.command]
    return handler(args.args)


def run() -> None:
    sys.exit(main())
