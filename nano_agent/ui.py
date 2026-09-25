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
