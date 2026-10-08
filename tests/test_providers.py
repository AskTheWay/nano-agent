"""Provider 层单测：协议转换纯函数 + 工厂选择（不触网）。"""

import json

import pytest

from nano_agent.llm import Usage, create_client
from nano_agent.providers import PROVIDERS, get_provider
from nano_agent.providers.anthropic_provider import (
    AnthropicProvider, from_anthropic, to_anthropic,
)
from nano_agent.providers.openai_provider import OpenAIProvider


def _msgs_case():
    """一段覆盖全部角色形态的规范格式会话。"""
    return [
        {"role": "system", "content": "你是 nano-agent"},
        {"role": "user", "content": "读 a.py"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "文件内容"},
        {"role": "assistant", "content": "a.py 的内容如下"},
    ]


# ========== 请求方向 to_anthropic ==========

def test_system_lifted_to_top_level():
    system, msgs, _ = to_anthropic(_msgs_case(), None)
    assert system == "你是 nano-agent"
    assert all(m["role"] in ("user", "assistant") for m in msgs)  # 不含 system


def test_tool_calls_become_tool_use_blocks():
    _, msgs, _ = to_anthropic(_msgs_case(), None)
    asst = next(m for m in msgs if m["role"] == "assistant" and isinstance(m["content"], list))
    tu = asst["content"][0]
    assert tu["type"] == "tool_use" and tu["id"] == "c1"
    assert tu["input"] == {"path": "a.py"}  # dict，不是 JSON 字符串


def test_consecutive_tools_merge_into_one_user_message():
    """同一轮的两个工具结果必须合并进同一条 user 消息（否则 API 400）。"""
    msgs = _msgs_case()[:2] + [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
            {"id": "c2", "type": "function",
             "function": {"name": "read_file", "arguments": '{"path": "b.py"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "A"},
        {"role": "tool", "tool_call_id": "c2", "content": "B"},
    ]
    _, out, _ = to_anthropic(msgs, None)
    # 只应有一条"工具结果 user 消息"，含两个 tool_result 块
    result_msgs = [m for m in out if m["role"] == "user" and isinstance(m["content"], list)]
    assert len(result_msgs) == 1
    assert [b["tool_use_id"] for b in result_msgs[0]["content"]] == ["c1", "c2"]


def test_tools_schema_conversion():
    tools = [{"type": "function", "function": {
        "name": "read_file", "description": "读文件",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}}]
    _, _, out = to_anthropic([], tools)
    assert out == [{"name": "read_file", "description": "读文件",
                    "input_schema": {"type": "object",
                                     "properties": {"path": {"type": "string"}},
                                     "required": ["path"]}}]


def test_bad_arguments_json_becomes_empty_dict():
    """规范格式的 arguments 是坏 JSON 时容错为空参数（不炸转换层）。"""
    msgs = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "x", "type": "function",
             "function": {"name": "t", "arguments": "{oops"}}]},
    ]
    _, out, _ = to_anthropic(msgs, None)
    assert out[0]["content"][0]["input"] == {}


# ========== 响应方向 from_anthropic ==========

class _Block:
    def __init__(self, type_, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


class _Msg:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage = usage


class _Usage:
    input_tokens = 100
    output_tokens = 20
    cache_read_input_tokens = 64
    cache_creation_input_tokens = 8


def test_from_anthropic_mixed_blocks():
    resp = _Msg([_Block("text", text="先看文件"),
                 _Block("tool_use", id="c1", name="read_file", input={"path": "a.py"})],
                _Usage())
    m, usage = from_anthropic(resp)
    assert m["content"] == "先看文件"
    assert m["tool_calls"][0]["function"]["arguments"] == '{"path": "a.py"}'  # JSON 字符串
    assert usage.prompt_tokens == 100 and usage.completion_tokens == 20
    assert usage.cached_tokens == 64  # cache_read -> cached


def test_from_anthropic_text_only():
    resp = _Msg([_Block("text", text="完成")], _Usage())
    m, usage = from_anthropic(resp)
    assert m == {"role": "assistant", "content": "完成"}
    assert "tool_calls" not in m


# ========== 工厂 ==========

class _Cfg:
    def __init__(self, base_url="", provider="auto"):
        self.base_url, self.provider = base_url, provider
        self.model, self.api_key, self.extra_body = "m", "k", None


def test_factory_explicit_choice():
    assert isinstance(get_provider(_Cfg(provider="openai")), OpenAIProvider)
    assert isinstance(get_provider(_Cfg(provider="anthropic")), AnthropicProvider)


def test_factory_auto_by_base_url():
    assert isinstance(get_provider(_Cfg(base_url="https://api.z.ai/api/anthropic")),
                      AnthropicProvider)
    assert isinstance(get_provider(_Cfg(base_url="https://open.bigmodel.cn/api/paas/v4")),
                      OpenAIProvider)


def test_factory_rejects_unknown():
    with pytest.raises(ValueError):
        get_provider(_Cfg(provider="gemini"))


def test_registry_and_backward_compat():
    assert "openai" in PROVIDERS
    from nano_agent.llm import LLMClient  # 旧调用名仍可用
    assert LLMClient is OpenAIProvider
    assert hasattr(create_client, "__call__")
