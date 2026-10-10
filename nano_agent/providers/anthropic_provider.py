"""Anthropic 协议 provider：messages 方言的双向适配器。

这是整个 provider 层最有教学价值的文件——两家协议的差异全部集中在这里：

┌────────────────────────┬──────────────────────────────────────┐
│ 规范格式（OpenAI 风格） │ Anthropic messages 协议                │
├────────────────────────┼──────────────────────────────────────┤
│ system 是 messages[0]  │ system 是【顶层字段】，不在 messages 里 │
│ content 永远是字符串    │ content 是【块数组】（text/tool_use/…）│
│ assistant.tool_calls   │ assistant 的 content 里的 tool_use 块  │
│   [{id, function:      │   [{type:"tool_use", id, name,         │
│     {name, arguments   │     input: <dict，不是 JSON 字符串>}]}  │
│     (JSON 字符串)}}]   │                                        │
│ tool 消息（独立角色）   │ user 消息里的 tool_result 块（连续的    │
│                        │ tool 消息要合并进同一条 user 消息）      │
│ tools[].function       │ tools[]: {name, description,           │
│   .parameters          │   input_schema}（无 function 包装层）   │
│ 无必填上限             │ max_tokens 【必填】                     │
└────────────────────────┴──────────────────────────────────────┘

请求路径：to_anthropic() 规范格式 -> Anthropic 协议
响应路径：from_anthropic() Anthropic 响应 -> 规范格式
两个函数都是纯函数（不触网）——所以可以完整单测。
"""

import json

from ..llm import Usage
from .base import BaseProvider

# Anthropic 要求 max_tokens 必填；教学版给一个宽裕的默认值
DEFAULT_MAX_TOKENS = 8192


# ========== 请求方向：规范格式 -> Anthropic 协议 ==========

def to_anthropic(messages: list[dict], tools: list[dict] | None
                 ) -> tuple[str | None, list[dict], list[dict]]:
    """转换整个会话。返回 (system, messages, tools)——Anthropic 的三件套。"""
    system_parts: list[str] = []
    out: list[dict] = []

    for m in messages:
        role = m["role"]
        if role == "system":
            # 差异 1：system 不进 messages，提到顶层（多个 system 拼接）
            system_parts.append(m.get("content") or "")

        elif role == "user":
            out.append({"role": "user", "content": m.get("content") or ""})

        elif role == "assistant":
            # 差异 2：content 变块数组；工具调用从 tool_calls 字段挪进块里
            blocks = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m.get("tool_calls", []):
                args = _safe_json_loads(tc["function"].get("arguments"))
                blocks.append({"type": "tool_use", "id": tc["id"],
                               "name": tc["function"]["name"], "input": args})
            out.append({"role": "assistant", "content": blocks or ""})

        elif role == "tool":
            # 差异 3：工具结果是 user 消息里的 tool_result 块。
            # 同一轮的多个工具结果必须合并进【同一条】user 消息——
            # 每条 tool 单独开一条 user 消息会被 API 拒绝（400）。
            block = {"type": "tool_result", "tool_use_id": m["tool_call_id"],
                     "content": m.get("content") or ""}
            prev = out[-1] if out else None
            if (prev and prev["role"] == "user" and isinstance(prev["content"], list)
                    and prev["content"] and prev["content"][0].get("type") == "tool_result"):
                prev["content"].append(block)  # 并入上一条
            else:
                out.append({"role": "user", "content": [block]})

    # 差异 4：工具定义拆掉 function 包装层，parameters 改名 input_schema
    tools_out = [
        {"name": t["function"]["name"],
         "description": t["function"].get("description", ""),
         "input_schema": t["function"].get("parameters",
                                           {"type": "object", "properties": {}})}
        for t in (tools or [])
    ]
    return ("\n".join(system_parts) or None), out, tools_out


def _safe_json_loads(s: str | None) -> dict:
    """arguments JSON 字符串 -> dict（容错：坏 JSON 当空参数）。"""
    try:
        v = json.loads(s or "{}")
        return v if isinstance(v, dict) else {}
    except json.JSONDecodeError:
        return {}


# ========== 响应方向：Anthropic 响应 -> 规范格式 ==========

def from_anthropic(resp_msg) -> tuple[dict, Usage]:
    """把 anthropic SDK 的 Message 对象转成规范格式。

    Anthropic 的 content 是块数组，可能同时含 text 和 tool_use
    （而 OpenAI 协议里文本在 content、调用在 tool_calls 两个字段）——
    遍历分拣即可。
    """
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in resp_msg.content:
        if block.type == "thinking":
            # 思考块：旁路存储到 _thinking（下划线 = 内部字段）。
            # 为什么不放进 content：a) 不污染下一轮发给模型的上下文；
            # b) 官方 Anthropic API 的 thinking 块回传有严格要求（须带
            #    signature 原样回传且仅在开启思考时允许），当普通 text
            #    塞回去会被拒。Z.AI/GLM 网关较宽容不强制，但保持规范。
            thinking_parts.append(getattr(block, "thinking", "") or "")
        elif block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            # input 是 dict -> 规范格式要求 JSON 字符串
            tool_calls.append({
                "id": block.id, "type": "function",
                "function": {"name": block.name,
                             "arguments": json.dumps(block.input or {},
                                                     ensure_ascii=False)},
            })
    m = {"role": "assistant", "content": "".join(text_parts)}
    if thinking_parts:
        m["_thinking"] = "\n".join(thinking_parts)  # 仅供观测/展示，不回传
    if tool_calls:
        m["tool_calls"] = tool_calls

    usage = Usage()
    if resp_msg.usage:
        u = resp_msg.usage
        usage.prompt_tokens = getattr(u, "input_tokens", 0) or 0
        usage.completion_tokens = getattr(u, "output_tokens", 0) or 0
        # Anthropic 的缓存字段是显式的：cache_read 即"命中"，
        # cache_creation 是本次新建缓存（下一轮才会命中）
        usage.cached_tokens = getattr(u, "cache_read_input_tokens", 0) or 0
    return m, usage


# ========== Provider 本体 ==========

class AnthropicProvider(BaseProvider):
    """messages 协议适配器。

    鉴权差异：Anthropic 用 x-api-key 头（SDK 封装为 api_key 参数），
    而不是 OpenAI 的 Authorization: Bearer。Z.AI 等网关两种头都收。
    """

    name = "anthropic"

    def __init__(self, cfg) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as e:
            raise ImportError(
                "使用 Anthropic 协议需要安装 SDK：pip install anthropic") from e
        self.model = cfg.model
        self.max_tokens = DEFAULT_MAX_TOKENS
        self._extra_body = cfg.extra_body  # 透传私有参数（如 {"thinking":{"type":"disabled"}}）
        self._client = Anthropic(base_url=cfg.base_url, api_key=cfg.api_key)

    def chat(self, messages: list[dict], tools: list[dict] | None = None
             ) -> tuple[dict, Usage]:
        system, msgs, tools_out = to_anthropic(messages, tools)
        kwargs = {}
        if self._extra_body:
            # SDK 的 extra_body：把私有参数注入请求体顶层（httpx 层，不做校验）
            kwargs["extra_body"] = self._extra_body
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,      # 必填，不传直接 400
            system=system,                    # 顶层字段（None 时 SDK 忽略）
            messages=msgs,
            tools=tools_out or None,
            **kwargs,
        )
        return from_anthropic(resp)
