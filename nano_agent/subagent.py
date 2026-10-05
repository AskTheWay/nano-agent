"""子代理：隔离上下文的子 loop（sidechain 思想）。

对标 Claude Code 的 Sub-agents 机制（官方文档"独立上下文窗口"一节）：
主 agent 通过 task 工具派生一个子 agent，子 agent 用【全新的消息历史】
跑完整循环，结束后【只把最终摘要】回传主上下文。

为什么要隔离：大范围调研（找文件/读十个文件/搜全库）会产生海量 tool 消息，
全塞进主上下文会 a) 挤占 token 预算 b) 稀释关键信息。子代理就像一个
"上下文沙箱"——脏活在里面干，外面只留结论。

三道闸（缺一不可）：
1. 只回传摘要：子代理的中间 tool 消息永不进入主 messages
2. 工具子集不含 task：结构性防递归（子代理不能再派孙代理）
3. 复用主 PermissionEngine：权限策略一致（子代理不能绕过 deny）
"""

from .agent import Agent, LoopConfig
from .context import estimate_messages
from . import ui

# ========== 子代理类型（对标官方的内建 subagent 类型） ==========

SUBAGENT_TYPES: dict[str, dict] = {
    "researcher": {
        "system_prompt": (
            "你是一个只读调研子代理（sidechain）。你有 read_file / glob / grep "
            "三个工具，负责替主代理完成信息收集与调研。\n"
            "要求：\n"
            "1. 高效定位：先用 glob/grep 缩小范围，再精读关键文件。\n"
            "2. 最终回答必须是自包含的调研摘要：结论 + 关键文件路径 + 重要发现，"
            "不依赖读者看过你的中间过程。\n"
            "3. 摘要控制在 500 字以内。"
        ),
        "allowed_tools": ["read_file", "glob", "grep"],
        "max_turns": 10,
    },
    # 教学版只做一种类型；官方还有 general-purpose / Plan / Explore 等，
    # 扩展方式就是往这个 dict 里加条目（system_prompt + 工具子集 + 轮数预算）
}


def run_task(llm, tools, permissions, description: str,
             sa_type: str = "researcher", bus=None) -> str:
    """派生一个子代理执行子任务，返回格式化的摘要（作为 task 工具的观测值）。"""
    if sa_type not in SUBAGENT_TYPES:
        return f"[错误] 未知子代理类型：{sa_type}（可用：{', '.join(SUBAGENT_TYPES)}）"

    spec = SUBAGENT_TYPES[sa_type]
    sub_tools = tools.subset(spec["allowed_tools"])  # 闸 2：子集不含 task

    print(f"{ui.CYAN}[task] 派生子代理 {sa_type}：{description[:60]}{ui.RESET}")
    if bus:  # 观测：sidechain 派生（面板⑥）
        bus.emit("subagent_spawn", sa_type=sa_type, description=description[:120])
    sub = Agent(
        llm=llm,
        tools=sub_tools,
        system_prompt=spec["system_prompt"],
        loop_cfg=LoopConfig(max_turns=spec["max_turns"]),
        permissions=permissions,          # 闸 3：复用主权限引擎
        indent="    ",                    # 缩进显示：sidechain 在终端上的可见性
        bus=bus,                          # 观测：子代理事件走同一总线（前端按来源分流）
    )
    summary = sub.run(description)        # 闸 1：全新 messages，与主上下文零共享

    n_tools = sum(1 for m in sub.messages if m["role"] == "tool")
    n_tokens = estimate_messages(sub.messages)
    print(f"{ui.CYAN}[task] 子代理完成（该上下文已丢弃，只回传摘要）{ui.RESET}")
    if bus:
        bus.emit("subagent_done", sa_type=sa_type, n_tools=n_tools,
                 tokens=n_tokens, summary=summary[:150])
    return (f"[子代理 {sa_type} 完成] 工具调用 {n_tools} 次 | "
            f"子上下文约 {n_tokens} tokens（已隔离，不占用主上下文）\n"
            f"---- 调研摘要 ----\n{summary}")
