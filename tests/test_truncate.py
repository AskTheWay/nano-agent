"""展示层截断单测 + 调度原序回填的逻辑验证（不触网）。"""

from nano_agent.ui import truncate_for_display


def test_short_passthrough():
    assert truncate_for_display("abc", limit=10) == "abc"


def test_long_truncated_with_marker():
    out = truncate_for_display("x" * 1000, limit=100)
    assert "回显截断" in out
    assert out.startswith("x" * 50)  # 前半保留


# ========== 调度铁律：并行结果按 tool_calls 原序回填 ==========

class _U:
    """最小 Usage 替身。"""

    def __init__(self, p, c):
        self.prompt_tokens, self.completion_tokens = p, c
        self.total = p + c


class FakeLLM:
    """两轮固定剧本：第一轮并行请求读两个文件，第二轮收尾。"""

    def __init__(self):
        self.n = 0

    def chat(self, messages, tools):
        self.n += 1
        if self.n == 1:
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "f1.txt"}'}},
                {"id": "c2", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "f2.txt"}'}},
            ]}, _U(10, 5)
        return {"role": "assistant", "content": "done"}, _U(10, 5)


def test_tool_result_order_preserved():
    """f1 故意慢 0.3s——若结果按完成序回填，f2 会排到前面；正确实现必须按原序。"""
    import time
    from nano_agent.agent import Agent, LoopConfig
    from nano_agent.tools import REGISTRY

    spec = REGISTRY.get("read_file")
    orig = spec.func

    def slow_read(path, offset=1, limit=0):
        if path == "f1.txt":
            time.sleep(0.3)
            return "SLOW:" + path
        return "FAST:" + path

    spec.func = slow_read  # dataclass 字段直接替换，测完还原
    try:
        agent = Agent(FakeLLM(), REGISTRY, "sys", LoopConfig(max_turns=3))
        agent.run("test")
    finally:
        spec.func = orig

    tools_msgs = [m for m in agent.messages if m["role"] == "tool"]
    assert len(tools_msgs) == 2
    assert tools_msgs[0]["tool_call_id"] == "c1"      # 原序：c1 在前
    assert tools_msgs[0]["content"] == "SLOW:f1.txt"  # 内容与 id 对应
    assert tools_msgs[1]["content"] == "FAST:f2.txt"
