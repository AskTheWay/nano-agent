"""LLM 调用封装：所有"OpenAI 兼容网关差异"的兜底都集中在这一个文件。

对应 M1 单文件里的"第 4 段：LLM 调用"。
"""

from dataclasses import dataclass

from openai import OpenAI


# ========== 用量统计（M3 的 ContextManager 会吃这个） ==========

@dataclass
class Usage:
    """一次请求的实际用量（API 返回，非估算）。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMClient:
    """薄薄一层 client.chat.completions.create——薄到能看清协议，厚到能兜住差异。"""

    def __init__(self, cfg) -> None:
        self.model = cfg.model
        self.extra_body = cfg.extra_body
        self._client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)

    def chat(self, messages: list[dict], tools: list[dict] | None = None) -> tuple[dict, Usage]:
        """发一次补全请求。

        返回 (assistant 消息裸 dict, usage)。

        兜底清单（各兼容网关的真实差异，实测踩出来的）：
        - tool_calls 为 None/缺失时【省略】该字段，取 [] 的兜底在 agent.py
          的 .get("tool_calls", []) —— 两层各管一段
        - arguments 是 JSON *字符串*               -> 解析容错放在 agent.py
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
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        usage = Usage()
        if resp.usage:
            usage.prompt_tokens = resp.usage.prompt_tokens or 0
            usage.completion_tokens = resp.usage.completion_tokens or 0
        return m, usage
