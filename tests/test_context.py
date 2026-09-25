"""token 估算单测（纯逻辑）。"""

from nano_agent.context import estimate_text, estimate_messages
from nano_agent.llm import _extract_cached


# ========== 缓存命中字段提取（各家协议字段名不同） ==========

class _OpenAIStyle:  # usage.prompt_tokens_details.cached_tokens
    class prompt_tokens_details:  # noqa: N801
        cached_tokens = 512


class _AnthropicStyle:  # usage.cache_read_input_tokens
    cache_read_input_tokens = 256


class _Bare:  # 智谱国内版：只有基础字段
    prompt_tokens = 100


def test_cached_extraction_all_dialects():
    assert _extract_cached(_OpenAIStyle()) == 512      # OpenAI 风格
    assert _extract_cached(_AnthropicStyle()) == 256   # Anthropic 风格
    assert _extract_cached(_Bare()) == 0               # 无字段 -> 0
    assert _extract_cached({"prompt_tokens_details": {"cached_tokens": 128}}) == 128
    assert _extract_cached({"cache_tokens": 64}) == 64  # OpenRouter dict 风格
    assert _extract_cached({}) == 0


def test_pure_english_quarter():
    """英文按 4 字符/token："abcd"*10 = 40 字符 -> 10 token。"""
    assert estimate_text("abcd" * 10) == 10


def test_pure_cjk():
    """中文按 1 token/字。"""
    assert estimate_text("你好世界") == 4


def test_mixed():
    # 4 个中文 + 8 个英文 = 4 + 2
    assert estimate_text("你好世界abcdefgh") == 6


def test_empty():
    assert estimate_text("") == 0


def test_message_overhead():
    """每条消息 +4 的协议包装开销；tool_calls 的 arguments 也计。"""
    msgs = [{"role": "user", "content": "abcd"}]          # 4 + 1
    assert estimate_messages(msgs) == 4 + 1
    msgs = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "function": {"name": "read_file", "arguments": '{"path": "abcd"}'}}]},
    ]  # 4 + 0 + schema 文本若干（>0）
    assert estimate_messages(msgs) > 4
