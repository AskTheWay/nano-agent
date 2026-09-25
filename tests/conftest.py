"""pytest 共享夹具。

- FakeLLM：可编程的假 LLM（按剧本回放 tool_calls，不触网）
- regISTRY 快照恢复：防止测试注册的工具污染其他测试/生产注册表
"""

import pytest

from nano_agent.llm import Usage
from nano_agent.tools import REGISTRY


class FakeLLM:
    """按剧本回放的假 LLM。

    script: list[dict] —— 每个 dict 是一轮响应：要么 {"text": "..."}（纯文本收尾），
    要么 {"calls": [ {"name":..., "args": {...}, "id": "c1"} ]}（发起工具调用）。
    剧本放完后再调用会抛 RuntimeError（测试剧本写错了）。
    """

    def __init__(self, script: list[dict] | None = None):
        self.script = script or [{"text": "done"}]
        self.n = 0

    def chat(self, messages, tools):
        if self.n >= len(self.script):
            raise RuntimeError("FakeLLM 剧本耗尽")
        step = self.script[self.n]
        self.n += 1
        if "calls" in step:
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": c.get("id", f"c{i}"), "type": "function",
                 "function": {"name": c["name"],
                              "arguments": __import__("json").dumps(
                                  c.get("args", {}), ensure_ascii=False)}}
                for i, c in enumerate(step["calls"])
            ]}, Usage(10, 5)
        return {"role": "assistant", "content": step.get("text", "")}, Usage(10, 5)


@pytest.fixture(autouse=True)
def _registry_snapshot():
    """每个测试前后快照/恢复全局注册表——测试里 install()/注册的残留
    不污染其他测试（实测不恢复会残留 'f'、'task' 等测试工具）。"""
    before = dict(REGISTRY._tools)
    yield
    REGISTRY._tools.clear()
    REGISTRY._tools.update(before)
