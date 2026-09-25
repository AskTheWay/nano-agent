"""工具包：import 触发注册（注册表靠 import 副作用收集工具）。

为什么这样设计：fs/search/shell 里的 @tool 装饰器在模块被 import 时执行，
REGISTRY 才有内容。代价是全局可变状态 + import 顺序敏感——
对照 bind_tools([...]) 的显式传参，这是 docs/02 讨论的真实取舍。

M5 会在末尾追加 task 工具的 import。
"""

from .registry import REGISTRY, ToolRegistry, ToolSpec, tool  # noqa: F401
from . import fs, search, shell, task  # noqa: F401  ← import 副作用就是注册动作
# 注意：task.py 的 task 工具不在 import 时注册（它需要 main 传入依赖闭包），
# main 组装时调用 task.install(llm, permissions) 完成注册。
