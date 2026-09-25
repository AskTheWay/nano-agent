"""shell 工具：bash —— subprocess + 超时 + 输出截断。

对标 Claude Code 的 Bash 工具。教学注意点：
- Windows 下 shell=True 走的是 cmd.exe，不是 bash——命令方言有差异
  （列目录用 dir、串联用 & 而不是 && 也行、没有 ls/grep）
- bash 里的 cd 改不了 agent 进程的工作目录：每次 subprocess 都在
  启动目录执行。要用绝对/相对路径，别指望 cd 生效（官方工具对 cwd 也有专门约定）
"""

import os
import subprocess
from typing import Annotated

from .registry import tool

# 给模型的输出上限（stdout 30k 字符：头尾保留）——bash 刷屏是上下文杀手
MAX_STDOUT = 30_000


@tool(read_only=False)
def bash(command: Annotated[str, "要执行的 shell 命令"]) -> str:
    """执行 shell 命令并返回 stdout/stderr/退出码（Windows 下为 cmd 方言）。"""
    # 每次调用时读环境变量：config 的 load_dotenv 一定发生在首次使用前
    timeout = int(os.environ.get("BASH_TIMEOUT", "10"))
    try:
        p = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            timeout=timeout,
            encoding="utf-8",       # 不指定的话 Windows 默认 GBK，中文输出直接炸
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return f"[错误] 命令超时（>{timeout}s）被强制终止：{command[:200]}"
    except OSError as e:
        return f"[错误] 无法启动命令：{e}"

    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if len(out) > MAX_STDOUT:  # 头尾保留的截断（和 read_file 同一思想）
        out = out[: MAX_STDOUT // 2] + f"\n...（省略 {len(out) - MAX_STDOUT} 字符）...\n" + out[-MAX_STDOUT // 4:]

    parts = [f"[exit {p.returncode}]"]  # 退出码是模型判断成败的关键信号
    if out:
        parts.append(out)
    if err:
        parts.append("[stderr]\n" + err[:2000])
    return "\n".join(parts)
