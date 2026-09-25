"""token 估算单测（纯逻辑）。"""

from nano_agent.context import estimate_text, estimate_messages


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
