"""文案相关子命令：stickman corpus / analysis / library / draft。

每个动作是一个 _cmd_* 函数，通过 set_defaults(handler=...) 挂到子命令上；错误统一由 _run 转成退出码。
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..collect.bilibili.storage import write_text
from .analysis import VERDICT_LABELS, AnalysisError, load_analyses, load_analysis, render_report, save_analysis
from .corpus import CorpusError, find_video, iter_videos, render_corpus, render_markdown
from .library import LibraryError, build_library, read_library, render_library, write_library
from .overlap import DEFAULT_MIN_CHARS, MAX_CITATION_CHARS, TARGET_CHARS, draft_length, find_overlaps

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NO_CORPUS = 2  # 没有可比较的文案：查重结果不可信，不能当作通过
DEFAULT_DATA_DIR = "data"
DEFAULT_ANALYSIS_DIR = "analysis"
DEFAULT_LIBRARY = "library/sources.jsonl"

Handler = Callable[[argparse.Namespace], int]


class CliError(Exception):
    """直接展示给用户的错误。"""


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _project_path(value: str) -> Path:
    """写入或读写的路径必须在项目目录（当前目录）内，避免被引导去覆盖别处的文件。"""
    root = Path.cwd().resolve()
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root):
        raise CliError(f"路径必须在项目目录内：{value}")
    return resolved


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("必须是正整数")
    return number


def _run(args: argparse.Namespace) -> int:
    try:
        return args.handler(args)
    except (CliError, CorpusError, AnalysisError, LibraryError, OSError) as exc:
        _err(f"错误：{exc}")
        return EXIT_FAILED


def _parser(prog: str, description: str) -> tuple[argparse.ArgumentParser, Any]:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    return parser, parser.add_subparsers(dest="action", required=True)


def _add(sub: Any, name: str, help_text: str, handler: Handler) -> argparse.ArgumentParser:
    command = sub.add_parser(name, help=help_text)
    command.set_defaults(handler=handler)
    return command


# ---------- stickman corpus


def _cmd_corpus_show(args: argparse.Namespace) -> int:
    print(render_markdown(find_video(args.data_dir, args.bvid)))
    return EXIT_OK


def _cmd_corpus_list(args: argparse.Namespace) -> int:
    for r in iter_videos(args.data_dir, mid=args.mid):
        print(f"{r.bvid}  {r.upload_date or '--------'}  {r.title}")
    return EXIT_OK


def _cmd_corpus_export(args: argparse.Namespace) -> int:
    records = iter_videos(args.data_dir, mid=args.mid)
    text = render_corpus(records)
    if args.output is None:
        print(text)
        return EXIT_OK
    write_text(_project_path(args.output), text)
    print(f"已导出 {len(records)} 篇到 {args.output}")
    return EXIT_OK


def corpus_main(argv: list[str]) -> int:
    parser, sub = _parser("stickman corpus", "读取已采集的文案")
    parser.add_argument("--data-dir", type=Path, default=Path(DEFAULT_DATA_DIR), help="采集数据目录（默认 data）")
    _add(sub, "show", "显示一个视频的文案和元数据", _cmd_corpus_show).add_argument("bvid")
    _add(sub, "list", "列出已有文案的视频", _cmd_corpus_list).add_argument("--mid", help="只看某个 up 主")
    export = _add(sub, "export", "导出所有文案为一份 Markdown", _cmd_corpus_export)
    export.add_argument("--mid", help="只导出某个 up 主")
    export.add_argument("--output", help="写入项目内的文件；不指定则输出到终端")
    return _run(parser.parse_args(argv))


# ---------- stickman analysis


def _read_json(source: str) -> object:
    raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise CliError(f"不是合法的 JSON：{exc}") from exc


def _verdict_counts(analysis: dict) -> str:
    counts: dict[str, int] = {}
    for claim in analysis["claims"]:
        counts[claim["verdict"]] = counts.get(claim["verdict"], 0) + 1
    return "、".join(f"{VERDICT_LABELS[v]} {n}" for v, n in counts.items()) or "无引用"


def _cmd_analysis_save(args: argparse.Namespace) -> int:
    analysis_dir = _project_path(args.analysis_dir)
    print(f"已保存：{save_analysis(_read_json(args.file), analysis_dir)}")
    return EXIT_OK


def _cmd_analysis_show(args: argparse.Namespace) -> int:
    print(render_report(load_analysis(_project_path(args.analysis_dir), args.bvid)))
    return EXIT_OK


def _cmd_analysis_list(args: argparse.Namespace) -> int:
    for a in load_analyses(_project_path(args.analysis_dir)):
        print(f"{a['bvid']}  {a['title']}  （{_verdict_counts(a)}）")
    return EXIT_OK


def analysis_main(argv: list[str]) -> int:
    parser, sub = _parser("stickman analysis", "保存和查看文案分析结果")
    parser.add_argument("--analysis-dir", default=DEFAULT_ANALYSIS_DIR, help="项目内目录，默认 analysis")
    save = _add(sub, "save", "校验并保存分析结果 JSON（文件路径，或 - 表示从标准输入读）", _cmd_analysis_save)
    save.add_argument("file")
    _add(sub, "show", "以报告形式显示分析结果", _cmd_analysis_show).add_argument("bvid")
    _add(sub, "list", "列出已有的分析结果", _cmd_analysis_list)
    return _run(parser.parse_args(argv))


# ---------- stickman library


def _cmd_library_build(args: argparse.Namespace) -> int:
    library = _project_path(args.library)
    entries = build_library(load_analyses(_project_path(args.analysis_dir)))
    write_library(entries, library)
    print(f"出处库：{len(entries)} 部作品 → {args.library}")
    return EXIT_OK


def _cmd_library_show(args: argparse.Namespace) -> int:
    entries = read_library(_project_path(args.library))
    if args.author:
        entries = [e for e in entries if args.author in e["author"]]
    print(render_library(entries))
    return EXIT_OK


def library_main(argv: list[str]) -> int:
    parser, sub = _parser("stickman library", "出处库：汇总所有分析里的原始出处")
    parser.add_argument("--analysis-dir", default=DEFAULT_ANALYSIS_DIR, help="项目内目录，默认 analysis")
    parser.add_argument("--library", default=DEFAULT_LIBRARY, help="项目内文件，默认 library/sources.jsonl")
    _add(sub, "build", "根据 analysis/ 重新生成出处库", _cmd_library_build)
    _add(sub, "show", "以 Markdown 显示出处库", _cmd_library_show).add_argument("--author", help="只看包含该作者名的作品")
    return _run(parser.parse_args(argv))


# ---------- stickman draft


def _print_length(draft: str) -> None:
    chars, seconds = draft_length(draft)
    low, high = TARGET_CHARS
    minutes, secs = divmod(round(seconds), 60)
    hint = "偏短" if chars < low else "偏长" if chars > high else "篇幅合适"
    print(f"正文 {chars} 字，约 {minutes} 分 {secs:02d} 秒（{hint}，风格指南建议 {low}–{high} 字）")


def _cmd_draft_check(args: argparse.Namespace) -> int:
    try:
        draft = args.file.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(f"读不到草稿：{exc}") from exc
    _print_length(draft)
    corpus = {r.bvid: r.text for r in iter_videos(args.data_dir, mid=args.mid)}
    if not corpus:
        _err(f"错误：{args.data_dir} 下没有已采集的文案，无法查重（检查 --data-dir / --mid）")
        return EXIT_NO_CORPUS
    overlaps = find_overlaps(draft, corpus, min_chars=args.min_chars)
    for o in overlaps:
        print(f"与 {o.bvid} 连续重合 {len(o.text)} 字：「{o.text}」")
    if overlaps:
        print(f"共 {len(overlaps)} 处重合，请改写（{MAX_CITATION_CHARS} 字以内的中文引号直接引用不计入）")
        return EXIT_FAILED
    print(f"未发现与 {len(corpus)} 篇已采集文案的连续重合（阈值 {args.min_chars} 字）")
    return EXIT_OK


def draft_main(argv: list[str]) -> int:
    parser, sub = _parser("stickman draft", "草稿工具")
    parser.add_argument("--data-dir", type=Path, default=Path(DEFAULT_DATA_DIR), help="采集数据目录（默认 data）")
    check = _add(sub, "check", "统计篇幅并与已采集文案查重；发现重合退出码 1，没有可比较的文案退出码 2", _cmd_draft_check)
    check.add_argument("file", type=Path)
    check.add_argument(
        "--min-chars", type=_positive_int, default=DEFAULT_MIN_CHARS, help=f"连续重合多少字算照搬（默认 {DEFAULT_MIN_CHARS}）"
    )
    check.add_argument("--mid", help="只和某个 up 主的文案比较")
    return _run(parser.parse_args(argv))
