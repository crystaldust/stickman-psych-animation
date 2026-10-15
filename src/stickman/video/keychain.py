"""从 macOS 钥匙串读 API key。只从钥匙串读，不读环境变量；key 只作为返回值，不打印、不进错误信息。"""

import subprocess
from collections.abc import Callable

from .errors import VideoError

Run = Callable[..., subprocess.CompletedProcess[str]]
# 钥匙串可能弹窗要求授权，留足时间。
TIMEOUT_S = 120


def read_secret(service: str, run: Run = subprocess.run) -> str:
    try:
        proc = run(["security", "find-generic-password", "-s", service, "-w"],
                   capture_output=True, text=True, timeout=TIMEOUT_S)
    except FileNotFoundError as exc:
        raise VideoError("找不到 security 命令（只支持 macOS 钥匙串）") from exc
    except subprocess.TimeoutExpired as exc:
        raise VideoError(f"读取钥匙串里的 {service} 超时") from exc
    secret = proc.stdout.strip() if proc.returncode == 0 else ""
    if not secret:
        raise VideoError(f"钥匙串里读不到 {service}（security 退出码 {proc.returncode}）")
    return secret
