"""OpenAI 协议 provider：chat.completions 方言（含一切兼容网关）。

从原 llm.py 的 LLMClient 迁移而来——逻辑零变化，只是换了个"户口"：
现在它只是 BaseProvider 的一种实现，不再是唯一的 LLM 入口。
"""

from openai import OpenAI

from ..llm import Usage, _extract_cached
from .base import BaseProvider


class OpenAIProvider(BaseProvider):
    """chat.completions 协议适配器。

    单例复用即连接池：self._client 是 httpx.Client（内建连接池），
    本实例存活期间所有请求共用它——这就是 provider 层全部的"池管理"。
    """

    name = "openai"

    def __init__(self, cfg) -> None:
        self.model = cfg.model
        self.extra_body = cfg.extra_body
        # 一个 provider 实例 = 一个长连接池；不要在 chat() 里新建 client
        self._client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)

    def chat(self, messages: list[dict], tools: list[dict] | None = None
             ) -> tuple[dict, Usage]:
        """规范格式恰好就是本协议 -> 直传即可（这就是选它当规范格式的红利）。

        兜底清单（各兼容网关的真实差异，实测踩出来的）：
        - tool_calls 为 None/缺失时【省略】该字段，取 [] 的兜底在 agent.py
        - arguments 是 JSON *字符串*                -> 解析容错放在 agent.py
        - 不传 parallel_tool_calls 参数             -> 一旦显式传（哪怕 false）
                                                    部分网关直接 400
        - schema 不用 strict 字段                   -> OpenAI 专属，兼容端会拒
        """
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools or None,  # 无工具时干脆不传该字段（有的网关对空数组敏感）
            extra_body=self.extra_body,
        )
        msg = resp.choices[0].message
        m = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            m["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name,
                                 "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        usage = Usage()
        if resp.usage:
            usage.prompt_tokens = resp.usage.prompt_tokens or 0
            usage.completion_tokens = resp.usage.completion_tokens or 0
            usage.cached_tokens = _extract_cached(resp.usage)
        return m, usage
