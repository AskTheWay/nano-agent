"""文件工具：read_file / write_file / edit_file。

对标 Claude Code 的 Read / Write / Edit 工具（截断策略与"唯一匹配替换"约定
见官方 Tools reference；这里是极简版）。
"""

import os
from typing import Annotated

from .registry import tool


# ========== 读：只读，可并行 ==========

@tool(read_only=True)
def read_file(path: Annotated[str, "文件路径，相对当前工作目录"],
              offset: Annotated[int, "起始行号，从 1 开始（按原始文件行号）"] = 1,
              limit: Annotated[int, "最多读取的行数，0 表示读到末尾"] = 0) -> str:
    """读取文本文件内容，带原始行号前缀；段超长时保留头尾并标注省略行数。

    顺序很关键：先按 offset/limit 从原始文件切片，后做展示截断——
    反过来的话 300 行文件的第 150 行永远读不到（截断后只剩 121 个元素），
    还会返回"第 150 行起没有内容"这种虚假错误。展示截断保留原始行号，
    模型引用行号才不会错位。
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        return f"[错误] 读取失败：{e}"
    total = len(lines)
    seg = lines[offset - 1: offset - 1 + limit] if limit else lines[offset - 1:]
    if not seg:
        return f"[错误] 第 {offset} 行起没有内容（文件共 {total} 行）"

    if len(seg) > 120:  # 给模型看的截断：头 100 + 尾 20（终端回显截断在 ui.py，两套阈值）
        numbered = [f"{offset + i:>5}  {line}" for i, line in enumerate(seg[:100])]
        numbered.append(f"      ...（省略 {len(seg) - 120} 行）...")
        numbered += [f"{offset + len(seg) - 20 + j:>5}  {line}"
                     for j, line in enumerate(seg[-20:])]
        return "\n".join(numbered)
    return "\n".join(f"{offset + i:>5}  {line}" for i, line in enumerate(seg))


# ========== 写：有副作用，必须串行 ==========

@tool(read_only=False)
def write_file(path: Annotated[str, "目标文件路径，父目录不存在会自动创建"],
               content: Annotated[str, "要写入的完整内容（整体覆盖）"]) -> str:
    """把内容整体写入文件（覆盖已存在的文件），自动创建父目录。"""
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        n_lines = content.count("\n") + (1 if content else 0)
        return f"已写入 {path}（{len(content)} 字符 / {n_lines} 行）"
    except OSError as e:
        return f"[错误] 写入失败：{e}"


# ========== 改：精确替换，唯一匹配才动手 ==========

@tool(read_only=False)
def edit_file(path: Annotated[str, "目标文件路径"],
              old_str: Annotated[str, "要被替换的原文，必须在文件中【唯一】匹配；"
                                      "不唯一时请扩大上下文范围（多带几行）"],
              new_str: Annotated[str, "替换后的新文本"]) -> str:
    """精确替换文件中的一段文本（str_replace 语义：old_str 必须唯一匹配）。"""
    try:
        # 读-改-写回路的编码纪律：surrogateescape 让无法解码的字节以 lone
        # surrogate 原样往返，回写时还原成原始字节——绝不触碰与本次编辑无关
        # 的内容。对比 errors='replace'：会把 GBK 等非 UTF-8 字节永久替换成
        # U+FFFD（不可逆损坏），哪怕 old_str/new_str 都是纯 ASCII。
        # newline="" 同理：不做 universal newlines 翻译，保留原始 CRLF/LF。
        with open(path, encoding="utf-8", errors="surrogateescape", newline="") as f:
            text = f.read()
    except OSError as e:
        return f"[错误] 读取失败：{e}"

    n = text.count(old_str)
    if n == 0:
        # 报错要"可行动"：告诉模型为什么失败、下一步该怎么办（错误即观测）
        return (f"[错误] old_str 在 {path} 中未找到。文件可能已被修改，"
                f"请重新 read_file 确认当前内容后再试。")
    if n > 1:
        return (f"[错误] old_str 在 {path} 中匹配到 {n} 处，不唯一。"
                f"请在 old_str 中多带几行上下文，使其只匹配一处。")
    try:
        with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(text.replace(old_str, new_str, 1))
    except OSError as e:
        return f"[错误] 写入失败：{e}"
    return f"已替换 1 处并保存（{len(old_str)} -> {len(new_str)} 字符）"
