"""权限×调度组合测试：DENY 观测 / y/n/a 分支 / ASK 归串行。"""

from conftest import FakeLLM
from nano_agent.agent import Agent, LoopConfig
from nano_agent.permissions import Decision, PermissionEngine, Rule
from nano_agent.tools import REGISTRY


def _engine(rules):
    e = PermissionEngine()
    e.allow = [Rule(r) for r in rules.get("allow", [])]
    e.deny = [Rule(r) for r in rules.get("deny", [])]
    e.default = Decision(rules.get("default", "ask"))
    return e


def _run(script, engine):
    agent = Agent(FakeLLM(script), REGISTRY, "sys",
                  LoopConfig(max_turns=3), permissions=engine)
    agent.run("go")
    return [m for m in agent.messages if m["role"] == "tool"]


def test_deny_returns_observation_without_confirm(monkeypatch):
    """DENY：不弹确认，直接拒绝观测。"""
    def no_confirm(*a, **k):
        raise AssertionError("DENY 不应弹确认")

    monkeypatch.setattr("nano_agent.ui.confirm", no_confirm)
    msgs = _run([{"calls": [{"name": "read_file", "args": {"path": ".env"}}]},
                 {"text": "ok"}],
                _engine({"deny": ["read_file(**/.env)"]}))
    assert "[权限拒绝]" in msgs[0]["content"]


def test_ask_confirm_yes_executes(monkeypatch):
    """ASK + y：放行执行。"""
    monkeypatch.setattr("nano_agent.ui.confirm", lambda *a, **k: "y")
    msgs = _run([{"calls": [{"name": "glob", "args": {"pattern": "*.nosuch"}}]},
                 {"text": "ok"}],
                _engine({}))  # default=ask
    assert "[权限拒绝]" not in msgs[0]["content"]  # 执行了（结果是无匹配）


def test_ask_confirm_no_denies(monkeypatch):
    """ASK + n：拒绝观测。"""
    monkeypatch.setattr("nano_agent.ui.confirm", lambda *a, **k: "n")
    msgs = _run([{"calls": [{"name": "glob", "args": {"pattern": "*.py"}}]},
                 {"text": "ok"}],
                _engine({}))
    assert "[权限拒绝]" in msgs[0]["content"]


def test_ask_confirm_always_remembered(monkeypatch):
    """ASK + a：本会话内同类放行（第二次不再弹确认）。"""
    calls = []
    real_engine = _engine({})

    def confirm_spy(*a, **k):
        calls.append(1)
        return "a"

    monkeypatch.setattr("nano_agent.ui.confirm", confirm_spy)
    agent = Agent(FakeLLM([
        {"calls": [{"name": "glob", "args": {"pattern": "*.py"}}]},
        {"calls": [{"name": "glob", "args": {"pattern": "*.md"}}]},
        {"text": "ok"},
    ]), REGISTRY, "sys", LoopConfig(max_turns=4), permissions=real_engine)
    agent.run("go")
    assert len(calls) == 1          # 只弹了一次
    assert len(real_engine.session_allow) == 1  # 记住了
    tools = [m for m in agent.messages if m["role"] == "tool"]
    assert "[权限拒绝]" not in tools[1]["content"]  # 第二次放行


def test_ask_tool_never_enters_parallel_group():
    """ASK 的调用必须归串行组（多线程弹 input 会竞争 stdin）。

    用'ASK 工具与只读工具同轮'的场景验证：不炸、有配对、都被执行。
    """
    import nano_agent.ui as ui
    orig_confirm = ui.confirm
    ui.confirm = lambda *a, **k: "y"
    try:
        msgs = _run([{"calls": [
            {"name": "glob", "args": {"pattern": "*.py"}},
            {"name": "write_file", "args": {"path": "sandbox/_perm_test.txt",
                                            "content": "x"}},
        ]}, {"text": "ok"}], _engine({"allow": ["glob"]}))
        assert len(msgs) == 2
        assert "已写入" in msgs[1]["content"]
    finally:
        ui.confirm = orig_confirm
