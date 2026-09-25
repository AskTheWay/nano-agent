"""子代理单测：上下文隔离 + 工具子集防递归（FakeLLM，不触网）。"""

from nano_agent.subagent import SUBAGENT_TYPES, run_task
from nano_agent.tools import REGISTRY
from conftest import FakeLLM


def test_researcher_tools_have_no_task():
    """闸 2：子代理的工具子集不含 task——结构性防递归。"""
    from nano_agent.tools import task as task_tool

    class _P:  # 权限替身（run_task 只透传给 Agent）
        pass

    task_tool.install(FakeLLM(), None)  # 先注册 task
    sub = REGISTRY.subset(SUBAGENT_TYPES["researcher"]["allowed_tools"])
    assert sub.get("read_file") is not None
    assert sub.get("task") is None  # 不能再派孙代理


def test_run_task_isolated_from_caller_context():
    """闸 1：子代理的中间消息不进入任何外部列表，只返回摘要文本。"""
    class SubFakeLLM:
        """子代理剧本：一轮 grep + 一轮收尾。"""

        def __init__(self):
            self.n = 0

        def chat(self, messages, tools):
            self.n += 1
            if self.n == 1:
                return {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "s1", "type": "function",
                     "function": {"name": "grep", "arguments": '{"pattern": "def "}'}},
                ]}, None
            return {"role": "assistant", "content": "调研结论：共 12 处函数定义。"}, None

    result = run_task(SubFakeLLM(), REGISTRY, permissions=None,
                      description="统计项目里有多少函数")
    assert "调研结论" in result          # 摘要在返回值里
    assert "[子代理 researcher 完成]" in result
    assert "工具调用 1 次" in result
    # 返回的只是字符串——子代理的 messages 随函数结束被丢弃（闭包内局部变量）


def test_unknown_subagent_type_errors():
    result = run_task(FakeLLM(), REGISTRY, permissions=None,
                      description="x", sa_type="writer")
    assert "[错误] 未知子代理类型" in result
