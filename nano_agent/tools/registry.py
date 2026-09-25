"""工具注册表：@tool 装饰器 + 从函数签名自动生成 JSON schema。

对应 M1 单文件里那段手写的 TOOLS_SCHEMA——那段代码有多痛苦，这里就有多甜：
每加一个工具，只需要写函数本身（类型注解 + Annotated 参数说明 + docstring），
schema 由 inspect 反射自动生成，再也不用在两处维护同一个信息。

这正是 LangChain bind_tools / Claude Agent SDK custom-tools 在底下做的事。
"""

import inspect
import typing
from dataclasses import dataclass, field
from typing import Annotated, Callable


# ========== 类型注解 -> JSON schema 类型 的映射表 ==========

TYPE_MAP: dict = {
    str: {"type": "string"},
    int: {"type": "integer"},
    float: {"type": "number"},
    bool: {"type": "boolean"},
    list[str]: {"type": "array", "items": {"type": "string"}},
    list[int]: {"type": "array", "items": {"type": "integer"}},
    list: {"type": "array"},
    dict: {"type": "object"},
}


def build_schema(func: Callable) -> dict:
    """从函数签名反射出 JSON schema parameters。

    约定（刻意的"够用即止"，见 docs/06 的取舍讨论）：
    - 参数说明用 Annotated[str, "说明"] 内联标注
    - 无类型注解 -> 宽松回退 string
    - 不支持嵌套对象 / Literal 枚举（需要时写进 description 让模型自己遵守）
    """
    sig = inspect.signature(func)
    props: dict = {}
    required: list[str] = []
    for name, p in sig.parameters.items():
        ann, desc = p.annotation, None
        # Annotated[str, "参数说明"] 拆包：拿到基础类型 + 抽出字符串元数据作描述
        if typing.get_origin(ann) is Annotated:
            base, *extras = typing.get_args(ann)
            ann = base
            desc = next((e for e in extras if isinstance(e, str)), None)
        schema = dict(TYPE_MAP.get(ann, {"type": "string"}))
        if desc:
            schema["description"] = desc
        props[name] = schema
        # 没有默认值的参数才是必填——和 Python 语义完全一致
        if p.default is inspect.Parameter.empty:
            required.append(name)
    return {"type": "object", "properties": props, "required": required}


# ========== 工具规格与注册表 ==========

@dataclass
class ToolSpec:
    """一个工具的全部信息。read_only 只用于本地调度，不会发给模型。"""

    name: str
    description: str           # 取 docstring 首段
    parameters: dict           # JSON schema
    func: Callable             # 执行体
    read_only: bool = False    # 只读工具可并行执行（见 agent.py 调度器）


class ToolRegistry:
    """全局工具表。schema() 的输出直接就是 API 的 tools 参数。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schema(self) -> list[dict]:
        """输出 OpenAI 协议的 tools 数组。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": s.name,
                    "description": s.description,
                    "parameters": s.parameters,
                },
            }
            for s in self._tools.values()
        ]

    def subset(self, names: list[str]) -> "ToolRegistry":
        """取子集注册表（M5 子代理用：只给只读工具，结构性防递归）。"""
        reg = ToolRegistry()
        for n in names:
            if (s := self._tools.get(n)):
                reg.register(s)
        return reg


# 全局单例：@tool 装饰器的注册目标。
# 代价是全局可变状态（import 顺序敏感）——对照 bind_tools([...]) 的显式传参，
# 这是一个真实的工程取舍，docs/02 有一节专门讨论。
REGISTRY = ToolRegistry()


def tool(read_only: bool = False) -> Callable:
    """装饰器：把普通函数变成注册好的工具。

    用法（fs.py 里的真实例子）：

        @tool(read_only=True)
        def read_file(path: Annotated[str, "文件路径"], limit: int = 0) -> str:
            \"\"\"读取文本文件内容。\"\"\"
    """
    def deco(func: Callable) -> Callable:
        REGISTRY.register(ToolSpec(
            name=func.__name__,
            description=(func.__doc__ or "").strip().split("\n\n")[0],  # docstring 首段
            parameters=build_schema(func),
            func=func,
            read_only=read_only,
        ))
        return func  # 原函数原样返回，测试可以直接调用（不需要经过注册表）
    return deco
