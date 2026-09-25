"""终端 UI 工具：Windows 编码、ANSI 颜色、展示截断、权限确认。

对应 M1 单文件里的 print 逻辑抽出来。注意一个设计点：
【给模型看的截断】和【给终端看的截断】是两套阈值——
模型要上下文完整性（read_file 头 100 行 + 尾 20 行），
人要可读性（回显最多几百字符）。
"""

import os
import sys

# ========== ANSI 颜色（不引 rich，够用就好） ==========

# 重定向到文件/管道时不输出转义序列（否则会原样落盘）。
# 注意：常量在 import 时求值，setup_console() 的 VT 唤醒发生在之前没关系——
# isatty() 判断的是流类型，与 VT 能力无关。
_COLOR_ON = sys.stdout.isatty()
DIM = "\x1b[2m" if _COLOR_ON else ""
CYAN = "\x1b[36m" if _COLOR_ON else ""
YELLOW = "\x1b[33m" if _COLOR_ON else ""
GREEN = "\x1b[32m" if _COLOR_ON else ""
RED = "\x1b[31m" if _COLOR_ON else ""
RESET = "\x1b[0m" if _COLOR_ON else ""


# ========== Windows 控制台三件套 ==========

def setup_console() -> None:
    """UTF-8 输入输出 + 唤醒 ANSI 颜色（VT 模式）。必须在入口最先调用。

    stdin 也要管：默认编码下管道喂 UTF-8 中文，input() 会读出乱码/
    孤立代理字符（Windows 默认按本机码页解码 stdin）。
    """
    for stream in (sys.stdout, sys.stdin):
        if stream.encoding and stream.encoding.lower() != "utf-8":
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError):
                pass  # 某些替换过的流不支持 reconfigure，尽力而为
    if os.name == "nt":
        os.system("")  # 空命令即可让 conhost 启用 ANSI 转义序列


def truncate_for_display(text: str, limit: int = 400) -> str:
    """给【终端】看的截断（区别于工具内部给【模型】的截断）。"""
    if len(text) <= limit:
        return text
    return text[: limit // 2] + f"\n{DIM}...（回显截断，共 {len(text)} 字符）{RESET}"


# ========== 权限确认交互（M4） ==========

def confirm(tool_name: str, args: dict) -> str:
    """终端 y/n/a 确认。返回 'y' / 'n' / 'a'（always，本会话内同类放行）。"""
    brief = ", ".join(f"{k}={str(v)[:80]!r}" for k, v in list(args.items())[:3])
    print(f"{YELLOW}  [权限确认] 即将执行 {tool_name}({brief}){RESET}")
    while True:
        try:
            ans = input("  允许执行吗？[y=允许 n=拒绝 a=本会话总是允许] ").strip().lower()
        except KeyboardInterrupt:
            return "n"  # 确认提示里按 Ctrl+C 视为拒绝（不打断 REPL）
        if ans in ("y", "n", "a"):
            return ans
        print("  请输入 y / n / a")
