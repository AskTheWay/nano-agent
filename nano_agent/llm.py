"""LLM 调用封装：所有"OpenAI 兼容网关差异"的兜底都集中在这一个文件。

对应 M1 单文件里的"第 4 段：LLM 调用"。
"""

from dataclasses import dataclass

from openai import OpenAI


# ========== 用量统计（M3 的 ContextManager 会吃这个） ==========

@dataclass
class Usage:
    """一次请求的实际用量（API 返回，非估算）。

    cached_tokens：prompt 里命中前缀缓存的部分（命中部分按约 0.1 倍计价，
    对 agent 这种每轮重发同一前缀的负载是成本大头）。各家字段名不统一：
    OpenAI 系在 usage.prompt_tokens_details.cached_tokens，Anthropic 系在
    usage.cache_read_input_tokens，智谱国内版不返回（实测恒无此字段）。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _extract_cached(usage_obj) -> int:
    """从原始 usage 对象里尽力提取缓存命中的 token 数（各家字段名不同）。"""
    # OpenAI 风格：usage.prompt_tokens_details.cached_tokens
    details = getattr(usage_obj, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", None)
    if cached:
        return cached
    # Anthropic 风格：usage.cache_read_input_tokens（经 OpenAI 兼容层时字段同名下放）
    cached = getattr(usage_obj, "cache_read_input_tokens", None)
    if cached:
        return cached
    # 兜底：原始 dict 形态（部分网关直接塞在 usage 里）
    if isinstance(usage_obj, dict):
        return int(usage_obj.get("cached_tokens")
                   or usage_obj.get("cache_tokens")
                   or (usage_obj.get("prompt_tokens_details") or {}).get("cached_tokens")
                   or 0)
    return 0


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
            usage.cached_tokens = _extract_cached(resp.usage)
        return m, usage
