"""shell 工具：bash —— subprocess + 超时 + 输出截断。

对标 Claude Code 的 Bash 工具。教学注意点：
- Windows 下 shell=True 走的是 cmd.exe，不是 bash——命令方言有差异
  （列目录用 dir、串联用 &、没有 ls/grep）
- bash 里的 cd 改不了 agent 进程的工作目录：每次 subprocess 都在
  启动目录执行。要用绝对/相对路径，别指望 cd 生效（官方工具对 cwd 也有专门约定）
"""

import locale
import os
import subprocess
from typing import Annotated

from .registry import tool

# 给模型的输出上限（stdout 30k 字符：头尾保留）——bash 刷屏是上下文杀手
MAX_STDOUT = 30_000


def _decode(b: bytes) -> str:
    """子进程输出的双码页回退解码。

    为什么要回退：跨平台工具（git、python -X utf8 等）向管道输出 UTF-8，
    而 cmd 内建命令和原生工具按【本机码页】输出（中文 Windows = GBK）。
    固定 utf-8 会把 cmd 的中文变成替换符；固定 GBK 会毁掉 git 的中文——
    谁先谁后都偏袒一方，所以先试 UTF-8 再退本机码页。
    """
    for enc in ("utf-8", locale.getpreferredencoding(False)):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode(locale.getpreferredencoding(False), errors="replace")


def _kill_tree(pid: int) -> None:
    """按进程树 kill。

    Windows 的坑：超时 kill 直接子进程（cmd.exe）后，孙进程（cmd 启动的
    python 等）不会死、管道不关闭——communicate 会一直阻塞到孙进程退出。
    taskkill /T 才能连树带根拔掉。
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


@tool(read_only=False)
def bash(command: Annotated[str, "要执行的 shell 命令"]) -> str:
    """执行 shell 命令并返回 stdout/stderr/退出码（Windows 下为 cmd 方言）。"""
    # 每次调用时读环境变量：config 的 load_dotenv 一定发生在首次使用前
    timeout = int(os.environ.get("BASH_TIMEOUT", "10"))
    try:
        p = subprocess.Popen(command, shell=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out_b, err_b = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(p.pid)
            p.communicate()  # 收尸：排空管道、回收进程，避免残留
            return f"[错误] 命令超时（>{timeout}s）被强制终止：{command[:200]}"
    except OSError as e:
        return f"[错误] 无法启动命令：{e}"

    out = _decode(out_b or b"").strip()
    err = _decode(err_b or b"").strip()
    if len(out) > MAX_STDOUT:  # 头尾保留的截断（和 read_file 同一思想）
        out = out[: MAX_STDOUT // 2] + f"\n...（省略 {len(out) - MAX_STDOUT} 字符）...\n" + out[-MAX_STDOUT // 4:]

    parts = [f"[exit {p.returncode}]"]  # 退出码是模型判断成败的关键信号
    if out:
        parts.append(out)
    if err:
        parts.append("[stderr]\n" + err[:2000])
    return "\n".join(parts)
