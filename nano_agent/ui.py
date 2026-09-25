"""终端 UI 工具：Windows 编码、ANSI 颜色、展示截断。

对应 M1 单文件里的 print 逻辑抽出来。注意一个设计点：
【给模型看的截断】和【给终端看的截断】是两套阈值——
模型要上下文完整性（read_file 头 100 行 + 尾 20 行），
人要可读性（回显最多几百字符）。
"""

import os
import sys

# ========== Windows 控制台三件套 ==========

def setup_console() -> None:
    """UTF-8 输出 + 唤醒 ANSI 颜色（VT 模式）。必须在入口最先调用。"""
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if os.name == "nt":
        os.system("")  # 空命令即可让 conhost 启用 ANSI 转义序列


# ========== ANSI 颜色（不引 rich，够用就好） ==========

DIM = "\x1b[2m"
CYAN = "\x1b[36m"
YELLOW = "\x1b[33m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
RESET = "\x1b[0m"


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
        ans = input("  允许执行吗？[y=允许 n=拒绝 a=本会话总是允许] ").strip().lower()
        if ans in ("y", "n", "a"):
            return ans
        print("  请输入 y / n / a")
