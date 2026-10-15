"""调用 ffmpeg / ffprobe。Runner 可替换，单元测试用假的，不真的跑 ffmpeg。"""

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from .errors import VideoError

Runner = Callable[[Sequence[str]], str]
MAX_ERROR_CHARS = 300


def run_command(cmd: Sequence[str]) -> str:
    """运行命令并返回 stdout；失败时只带 stderr 的最后一段，避免把整屏日志塞进错误信息。"""
    args = [str(c) for c in cmd]
    try:
        # stdin 必须断开：并行的 ffmpeg 会抢终端输入，甚至被 SIGTTIN 挂起。
        proc = subprocess.run(args, capture_output=True, text=True, errors="replace", stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise VideoError(f"找不到 {args[0]}，请先安装 ffmpeg（brew install ffmpeg）") from exc
    if proc.returncode:
        raise VideoError(f"{args[0]} 失败（退出码 {proc.returncode}）：{proc.stderr.strip()[-MAX_ERROR_CHARS:]}")
    return proc.stdout


def ffmpeg(args: Sequence[str | Path], runner: Runner) -> None:
    """最后一个参数是输出文件。"""
    runner(["ffmpeg", "-y", "-loglevel", "error", *[str(a) for a in args]])


def probe_duration(path: str | Path, runner: Runner) -> float:
    out = runner(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    try:
        return float(out.strip())
    except ValueError as exc:
        raise VideoError(f"读不出 {path} 的时长：{out.strip()[:80]}") from exc


def count_frames(path: str | Path, runner: Runner) -> int:
    out = runner(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                  "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)])
    try:
        return int(out.strip())
    except ValueError as exc:
        raise VideoError(f"读不出 {path} 的帧数：{out.strip()[:80]}") from exc


def filter_escape(path: str | Path) -> str:
    """把文件路径放进滤镜参数（如 subtitles=...）：先按选项值转义，再按滤镜图转义。"""
    value = str(path)
    for ch in "\\':":
        value = value.replace(ch, "\\" + ch)
    for ch in "\\'[],;":
        value = value.replace(ch, "\\" + ch)
    return value


def concat_line(path: str | Path) -> str:
    """concat demuxer 列表里的一行；单引号按 shell 规则转义。"""
    return "file '" + str(path).replace("'", "'\\''") + "'\n"
