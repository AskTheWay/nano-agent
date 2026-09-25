"""Agent 主循环核心机制单测：max_turns / 重复检测 / 错误观测 / 协议兜底。"""

from conftest import FakeLLM
from nano_agent.agent import Agent, LoopConfig
from nano_agent.tools import REGISTRY
from nano_agent.tools.registry import tool


def _tool_msgs(agent):
    return [m for m in agent.messages if m["role"] == "tool"]


def test_max_turns_force_stop():
    """无限工具调用 -> max_turns 强制终止。"""
    llm = FakeLLM([{"calls": [{"name": "glob"}]}] * 5)  # 每轮都发起调用
    agent = Agent(llm, REGISTRY, "sys", LoopConfig(max_turns=3))
    result = agent.run("go")
    assert "强制中止" in result
    assert agent.llm.n == 3  # 只跑了 3 轮


def test_repeat_detection_injects_hint_after_tool_results():
    """连续 3 轮相同调用 -> 注入纠偏 user 消息，且位置在 tool 回填之后
    （插在 assistant(tool_calls) 与 tool 之间会破坏协议配对）。"""
    call = {"calls": [{"name": "glob", "args": {"pattern": "*.py"}}]}
    llm = FakeLLM([call, call, call, {"text": "ok"}])
    agent = Agent(llm, REGISTRY, "sys", LoopConfig(max_turns=6, repeat_limit=3))
    agent.run("go")

    hints = [m for m in agent.messages if m["role"] == "user" and "系统提示" in (m.get("content") or "")]
    assert len(hints) == 1
    i = agent.messages.index(hints[0])
    # 纠偏消息的前一条必须是 tool（第三轮的回填已完成）
    assert agent.messages[i - 1]["role"] == "tool"


def test_unknown_tool_becomes_observation():
    llm = FakeLLM([
        {"calls": [{"name": "no_such_tool", "args": {"x": 1}}]},
        {"text": "ok"},
    ])
    agent = Agent(llm, REGISTRY, "sys", LoopConfig(max_turns=3))
    agent.run("go")
    assert "[错误] 未知工具：no_such_tool" in _tool_msgs(agent)[0]["content"]


def test_tool_exception_becomes_observation():
    """工具内部炸了不打断循环，转成错误观测。"""
    @tool(read_only=True)
    def bomb(path: str) -> str:
        """炸弹。"""
        raise ValueError("炸了")

    llm = FakeLLM([
        {"calls": [{"name": "bomb", "args": {"path": "x"}}]},
        {"text": "ok"},
    ])
    agent = Agent(llm, REGISTRY, "sys", LoopConfig(max_turns=3))
    agent.run("go")
    content = _tool_msgs(agent)[0]["content"]
    assert "工具执行异常" in content and "炸了" in content


def test_bad_json_arguments_still_get_paired_result_in_order():
    """一轮里 [非法 JSON, 正常调用] -> 两条 tool 消息按原序，非法的那条有错误观测。"""
    import json
    llm = FakeLLM__raw_calls()
    agent = Agent(llm, REGISTRY, "sys", LoopConfig(max_turns=3))
    agent.run("go")
    msgs = _tool_msgs(agent)
    assert len(msgs) == 2
    assert msgs[0]["tool_call_id"] == "bad"
    assert "不是合法 JSON" in msgs[0]["content"]
    assert msgs[1]["tool_call_id"] == "ok"


def FakeLLM__raw_calls():
    """构造一轮含非法 arguments 的调用（FakeLLM 只会生成合法 JSON，手工拼）。"""
    llm = FakeLLM([{"text": "placeholder"}])

    def chat(messages, tools):
        if llm.n == 0:
            llm.n = 1
            return {"role": "assistant", "content": "", "tool_calls": [
                {"id": "bad", "type": "function",
                 "function": {"name": "glob", "arguments": "{oops"}},
                {"id": "ok", "type": "function",
                 "function": {"name": "glob", "arguments": '{"pattern": "*.py"}'}},
            ]}, None
        llm.n += 1
        return {"role": "assistant", "content": "done"}, None

    llm.chat = chat
    return llm


def test_exception_escape_still_pairs_all_calls(monkeypatch):
    """_execute_calls 中途异常逃逸 -> finally 兜底给每个 call 补配对结果。"""
    llm = FakeLLM([
        {"calls": [
            {"name": "glob", "args": {"pattern": "*.py"}, "id": "c1"},
            {"name": "glob", "args": {"pattern": "*.md"}, "id": "c2"},
        ]},
        {"text": "done"},
    ])
    agent = Agent(llm, REGISTRY, "sys", LoopConfig(max_turns=3))

    def explode(tc, args):
        raise RuntimeError("调度器内部异常")

    monkeypatch.setattr(agent, "_call_one", explode)
    try:
        agent.run("go")
        raised = False
    except RuntimeError:
        raised = True
    assert raised  # 异常继续传播（REPL 兜底），但——
    msgs = _tool_msgs(agent)
    assert len(msgs) == 2  # 两个 call 都有配对结果（协议不破）
    assert all("执行中断" in m["content"] for m in msgs)
