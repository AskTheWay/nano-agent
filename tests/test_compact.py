"""压缩算法单测：安全边界 + 压缩后协议合法（注入假摘要，不触网）。"""

from nano_agent.context import ContextManager, find_safe_boundary


def _mk_messages():
    """构造一段典型的历史：system + 3 个完整的 user 轮（带工具调用）。"""
    msgs = [{"role": "system", "content": "sys"}]
    for n in range(1, 4):
        msgs += [
            {"role": "user", "content": f"第{n}问"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{n}", "type": "function",
                 "function": {"name": "read_file", "arguments": f'{{"path": "{n}.py"}}'}}]},
            {"role": "tool", "tool_call_id": f"c{n}", "content": f"文件{n}内容"},
            {"role": "assistant", "content": f"第{n}答"},
        ]
    return msgs


def test_boundary_never_orphans_tool_message():
    """保留段起点必须是 user 消息——tool/assistant(tool_calls) 不会被劈开。"""
    msgs = _mk_messages()
    b = find_safe_boundary(msgs, keep_recent=6)
    assert msgs[b]["role"] == "user"
    # 无论 keep_recent 怎么变，边界永远落在 user 上（或退到 1）
    for k in range(1, 12):
        b = find_safe_boundary(msgs, keep_recent=k)
        assert b == 1 or msgs[b]["role"] == "user"


def test_boundary_respects_keep_recent():
    msgs = _mk_messages()  # 13 条：system + 3 轮（每轮 user/as(tc)/tool/as）
    # keep_recent 大到覆盖全部对话时，只能压缩 index 1（紧邻 system 的第一条）
    assert find_safe_boundary(msgs, keep_recent=13) == 1
    # keep_recent 小时，边界后移到第三个 user（index 9）
    assert find_safe_boundary(msgs, keep_recent=2) == 9


def test_compact_result_protocol_valid():
    """压缩后：system 仍在首位、boundary 标记存在、无孤儿 tool 消息。"""
    cm = ContextManager(_mk_messages(), llm=None, token_limit=100)
    cm.summarizer = lambda script: "假摘要"  # 注入假摘要，不触网
    cm.compact_now()

    m = cm.messages
    assert m[0]["role"] == "system"
    assert "[compact_boundary]" in m[1]["content"]
    assert cm.compact_count == 1

    # 协议合法性：每条带 tool_calls 的 assistant 之后紧跟配对的 tool 消息
    for i, msg in enumerate(m):
        if msg.get("tool_calls"):
            nxt = m[i + 1]
            assert nxt["role"] == "tool"
            assert nxt["tool_call_id"] == msg["tool_calls"][0]["id"]


def test_compact_shrinks_messages():
    cm = ContextManager(_mk_messages(), llm=None, token_limit=100)
    cm.summarizer = lambda s: "短摘要"
    holder = cm.messages  # 模拟 Agent 持有的同一引用
    before = len(cm.messages)
    cm.compact_now()
    assert len(cm.messages) < before  # 13 条 -> system+boundary+保留段
    # 原地替换（messages[:] = ...）的验证：外部引用仍指向同一个 list 对象。
    # 若实现错写成 self.messages = [...]（改绑定），该断言失败——
    # Agent 那边就会指着旧列表，压缩静默失效。
    assert cm.messages is holder


def test_threshold_triggers_compact():
    """估算超预算 80% 时 maybe_compact 自动触发。"""
    cm = ContextManager(_mk_messages(), llm=None, token_limit=10)  # 预算极小
    cm.summarizer = lambda s: "假摘要"
    cm.maybe_compact()
    assert cm.compact_count == 1

    cm2 = ContextManager(_mk_messages(), llm=None, token_limit=10_000)  # 预算巨大
    cm2.summarizer = lambda s: "假摘要"
    cm2.maybe_compact()
    assert cm2.compact_count == 0
