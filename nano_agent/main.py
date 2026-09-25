"""nano-agent 入口：REPL + 斜杠命令分发。

M2 形态：M1 的单文件被拆成 config/llm/agent/tools/ui 六个模块后，
main.py 只剩"组装 + 交互"两件事——这正是入口层应该有的厚度。
（M1 -> M2 的拆分映射表见 docs/02-tools.md 开头。）
"""

import platform

from . import ui
from .config import load_config
from .llm import LLMClient


# ========== system prompt：能力的一半在工具，另一半在这里 ==========

def build_system_prompt() -> str:
    """角色 + 环境注入 + 工具使用规范（Claude Code 同样注入 cwd/OS/git 状态）。"""
    return f"""你是一个运行在终端里的编程助手 nano-agent（教学项目）。回答用中文，简洁。

环境信息：
- 工作目录：{_cwd()}
- 操作系统：{platform.system()} {platform.release()}（shell 命令按此系统的方言执行）

可用工具：read_file / write_file / edit_file / glob / grep / bash

工作规范：
1. 动手改文件前，先 read_file 看当前内容，不要凭猜测直接写。
2. 优先用 glob/grep 定位文件，再精确读取，避免整目录扫读。
3. edit_file 的 old_str 必须唯一匹配；如果报错，按提示扩大上下文重试。
4. 工具返回 [错误] 开头的内容时是正常的观测结果，换个做法继续，不要原样重试。
5. 不确定的事情先用工具确认，不要编造文件内容或命令输出。"""


def _cwd() -> str:
    import os
    return os.getcwd()


# ========== 斜杠命令：本地拦截，不进模型 ==========

def _cmd_help(agent) -> None:
    print("命令：/help /exit /clear /tools /history [n]（M3 会加 /tokens /compact）")


def _cmd_tools(agent) -> None:
    for name in agent.tools.names():
        spec = agent.tools.get(name)
        tag = f"{ui.CYAN}只读{ui.RESET}" if spec.read_only else f"{ui.YELLOW}写{ui.RESET}"
        print(f"  {name:<12} {tag}  {spec.description[:40]}")


def _cmd_history(agent, n: int = 6) -> None:
    """渲染最近 n 条消息（role 着色，tool 消息截断显示）。"""
    color = {"system": ui.DIM, "user": ui.CYAN, "assistant": ui.GREEN, "tool": ui.DIM}
    for m in agent.messages[-n:]:
        head = f"{color.get(m['role'], '')}{m['role']}{ui.RESET}"
        body = m.get("content") or f"<tool_calls: {len(m.get('tool_calls', []))} 个>"
        print(f"  {head:<12} {ui.truncate_for_display(body, 160)}")


def _cmd_clear(agent) -> None:
    system = agent.messages[0]
    agent.messages[:] = [system]  # 原地替换保引用（Agent 持有的是同一个 list）
    print("（已清空对话历史，仅保留 system）")


COMMANDS = {
    "/help": _cmd_help,
    "/tools": _cmd_tools,
    "/history": _cmd_history,
    "/clear": _cmd_clear,
}


# ========== REPL 主循环 ==========

def main() -> None:
    ui.setup_console()
    cfg = load_config()

    from .agent import Agent, LoopConfig          # noqa: E402
    from .tools import REGISTRY                   # noqa: E402  import 副作用完成工具注册

    agent = Agent(
        llm=LLMClient(cfg),
        tools=REGISTRY,
        system_prompt=build_system_prompt(),
        loop_cfg=LoopConfig(max_turns=cfg.max_turns),
    )

    print("=" * 56)
    print(f"  nano-agent M2 | 模型：{cfg.model}")
    print(f"  工具：{', '.join(REGISTRY.names())}")
    print(f"  输入 /help 查看命令")
    print("=" * 56)

    while True:
        try:
            user_input = input("\n你> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见。")
            break
        if not user_input:
            continue

        # 斜杠命令在本地拦截，根本不会发给模型（官方 slash commands 同理）
        if user_input.startswith("/"):
            parts = user_input.split()
            cmd, args = parts[0], parts[1:]
            if cmd in ("/exit", "/quit"):
                print("再见。")
                break
            handler = COMMANDS.get(cmd)
            if handler is None:
                print(f"未知命令 {cmd}，/help 查看可用命令")
            else:
                handler(agent, *args)  # /history 5 这类带参命令
            continue

        try:
            answer = agent.run(user_input)
        except Exception as e:  # 网络/鉴权错误兜底，REPL 不崩
            print(f"\n{ui.RED}[出错了] {e}{ui.RESET}\n（检查 .env 配置与网络后重试）")
            continue
        print(f"\n{ui.GREEN}nano-agent>{ui.RESET} {answer}")


if __name__ == "__main__":
    main()
