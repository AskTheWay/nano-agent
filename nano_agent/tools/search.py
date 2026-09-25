"""搜索工具：glob / grep —— 纯标准库实现，不依赖 ripgrep。

对标 Claude Code 的 Glob / Grep 工具。给模型的截断：glob 最多 200 条、
grep 最多 50 个匹配（真实项目里这两个数字小到模型消化得动就够）。
"""

import fnmatch
import glob as globlib
import os
import re
from typing import Annotated

from .registry import tool

# 目录黑名单：搜索永远不该钻进这些地方
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".pytest_cache"}


@tool(read_only=True)
def glob(pattern: Annotated[str, "glob 模式，如 '**/*.py'（** 表示递归任意层）"],
        path: Annotated[str, "搜索的根目录，默认当前目录"] = ".") -> str:
    """按 glob 模式列出匹配的文件路径（递归），按路径排序。"""
    if not os.path.isdir(path):
        return f"[错误] 目录不存在：{path}"
    matches = sorted(
        p for p in globlib.glob(os.path.join(path, pattern), recursive=True)
        if os.path.isfile(p) and not any(s in p for s in SKIP_DIRS)
    )
    if not matches:
        return "(无匹配文件)"
    if len(matches) > 200:
        matches = matches[:200] + [f"...（共 {len(matches)} 条，只显示前 200）"]
    return "\n".join(matches)


@tool(read_only=True)
def grep(pattern: Annotated[str, "正则表达式，如 'def \\w+'"],
         path: Annotated[str, "搜索的根目录，默认当前目录"] = ".",
         include: Annotated[str, "文件名过滤的 glob 模式，如 '*.py'"] = "*") -> str:
    """在文件内容中递归搜索正则，输出 文件:行号: 行内容。"""
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"[错误] 正则表达式无效：{e}"

    hits: list[str] = []
    truncated = False
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in sorted(files):
            if not fnmatch.fnmatch(fn, include):
                continue
            fp = os.path.join(root, fn)
            try:
                if os.path.getsize(fp) > 1_000_000:  # 跳过超大文件
                    continue
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, 1):
                        if rx.search(line):
                            hits.append(f"{fp}:{lineno}: {line.rstrip()}")
                            if len(hits) >= 50:
                                truncated = True
                                break
            except OSError:
                continue
            if truncated:
                break
    if not hits:
        return "(无匹配)"
    if truncated:
        hits.append("...（匹配超过 50 条，已截断——请用更精确的 pattern 或 include）")
    return "\n".join(hits)
