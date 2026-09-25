"""build_schema 单测：注册表的核心逻辑（不触网、不依赖 LLM）。"""

from typing import Annotated

from nano_agent.tools.registry import REGISTRY, build_schema, tool


def _f_plain(a, b=1):
    """首段描述。

    第二段不应出现在 description 里。
    """
    return a + b


def test_required_and_optional():
    """无默认值 -> required；有默认值 -> 可选。与 Python 语义一致。"""
    schema = build_schema(_f_plain)
    assert schema["required"] == ["a"]
    assert "b" not in schema["required"]
    assert set(schema["properties"]) == {"a", "b"}


def test_no_annotation_falls_back_to_string():
    schema = build_schema(_f_plain)
    assert schema["properties"]["a"] == {"type": "string"}


def test_annotated_description_injected():
    def g(path: Annotated[str, "文件路径"], limit: int = 0):
        """读文件。"""
        return path

    schema = build_schema(g)
    assert schema["properties"]["path"]["description"] == "文件路径"
    assert schema["properties"]["path"]["type"] == "string"
    assert schema["properties"]["limit"] == {"type": "integer"}


def test_type_map():
    def h(items: list[str], ratio: float = 0.5, flag: bool = False):
        """混合类型。"""
        return items

    p = build_schema(h)["properties"]
    assert p["items"] == {"type": "array", "items": {"type": "string"}}
    assert p["ratio"]["type"] == "number"
    assert p["flag"]["type"] == "boolean"


def test_docstring_first_paragraph_is_description():
    def f(x: int):
        """首段描述。

        第二段内容不应出现。
        """
        return x

    deco = tool(read_only=True)(f)
    spec = REGISTRY.get("f")
    assert spec.description == "首段描述。"
    assert spec.read_only is True
    assert deco is f  # 装饰器原样返回函数


def test_registered_tool_schema_shape():
    """已注册工具的 schema 输出符合 OpenAI 协议形状。"""
    schema = REGISTRY.schema()
    assert any(s["function"]["name"] == "read_file" for s in schema)
    one = next(s for s in schema if s["function"]["name"] == "read_file")
    assert one["type"] == "function"
    assert one["function"]["parameters"]["type"] == "object"
