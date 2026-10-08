"""LLM 层的门面：Usage 统计 + 工厂入口。

历史沿革：M1~M5 这里是 LLMClient（OpenAI 协议直连）；provider 层拆出后，
它只剩两个职责：
1. Usage / _extract_cached —— 厂商无关的用量抽象（各 provider 共用）
2. create_client(cfg)      —— 工厂入口，main/webui 组装时调用

厂商差异的实现全部在 nano_agent/providers/ 下（见 docs/08-providers.md）。
"""

import json
from dataclasses import dataclass


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


# ========== 工厂入口 ==========

def create_client(cfg):
    """按配置构造 provider（openai / anthropic / auto 按 base_url 猜）。

    返回值满足 BaseProvider 契约：chat(messages, tools) -> (dict, Usage)。
    """
    from .providers import get_provider
    return get_provider(cfg)


# 兼容别名：M1~M5 期间的调用写法 LLMClient(cfg) 仍然可用（测试/旧代码）
from .providers.openai_provider import OpenAIProvider as LLMClient  # noqa: E402,F401
