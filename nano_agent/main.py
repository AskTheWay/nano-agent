"""
nano-agent M1：最小 Agent Loop —— 一个文件看懂框架底下发生了什么
================================================================

你已经用 LangGraph 写过 ReAct agent（StateGraph / bind_tools / conditional_edges）。
这个文件把同一件事用 ~250 行裸 Python 重写一遍，你会亲眼看到：

    StateGraph + conditional_edges  ==>  一个 while 循环 + 一个 if
    bind_tools([search, ...])       ==>  一段手写的 JSON schema + tool_calls 解析
    add_messages                    ==>  messages.append(...)
    AIMessage / ToolMessage         ==>  两个普通 dict

消息历史就是 OpenAI 协议原生的 list[dict]——LangChain 的消息类只是这些 dict 的糖衣。

运行：python -m nano_agent
"""

import json
import os
import platform
import subprocess  # noqa: F401  （M2 的 bash 工具会用，这里先占位说明）
import sys

# ========== 1. 环境与配置 ==========

# Windows 控制台三件套：UTF-8 输出、唤醒 ANSI 颜色（VT 模式）
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if os.name == "nt":
    os.system("")  # 空命令即可让 conhost 启用 ANSI 转义序列

from dotenv import load_dotenv  # noqa: E402

load_dotenv()  # 读取项目根目录的 .env（BASE_URL / API_KEY / MODEL_NAME）

BASE_URL = os.environ.get("OPENAI_BASE_URL", "")
API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("MODEL_NAME", "")
MAX_TURNS = int(os.environ.get("MAX_TURNS", "25"))  # 防无限循环的第一道闸

# 部分网关需要额外请求体（如 mimo 关闭思考模式），用 JSON 字符串传入
_extra = os.environ.get("OPENAI_EXTRA_BODY", "").strip()
EXTRA_BODY = json.loads(_extra) if _extra else None

if not (BASE_URL and API_KEY and MODEL):
    print("[启动失败] 缺少模型配置。请复制 .env.example 为 .env 并填写：")
    print("  OPENAI_BASE_URL   端点地址（mimo/智谱/DeepSeek/OpenRouter 均可）")
    print("  OPENAI_API_KEY    API key")
    print("  MODEL_NAME        模型名")
    sys.exit(1)

from openai import OpenAI  # noqa: E402

client = OpenAI(base_url=BASE_URL, api_key=API_KEY)


# ========== 2. 工具实现（纯函数：接收参数，返回字符串） ==========
# 工具的"执行体"就是这么朴素的函数——没有任何魔法。

def read_file(path: str) -> str:
    """读取文本文件内容；超长时保留头尾并标注省略行数（避免撑爆上下文）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError as e:
        return f"[错误] 读取失败：{e}"
    if len(lines) > 120:  # 头 100 行 + 尾 20 行——"给模型看的"截断阈值
        kept = lines[:100] + [f"... 省略 {len(lines) - 120} 行 ..."] + lines[-20:]
        return "\n".join(kept)
    return "\n".join(lines)


def list_dir(path: str = ".") -> str:
    """列出目录内容（带类型标记）。"""
    try:
        entries = sorted(os.listdir(path))
    except OSError as e:
        return f"[错误] 列目录失败：{e}"
    out = []
    for name in entries:
        full = os.path.join(path, name)
        out.append(f"{'[目录]' if os.path.isdir(full) else '[文件]'} {name}")
    return "\n".join(out) or "(空目录)"


TOOL_FUNCS = {"read_file": read_file, "list_dir": list_dir}

# ========== 3. 工具 schema —— 手写！ ==========
# 这就是 bind_tools([read_file, list_dir]) 替你生成的东西。
# 感受一下：每加一个工具，这里要手抄一遍函数名、参数、类型、描述——
# 又长、又易错、和上面的函数实现割裂（M2 我们用装饰器 + inspect 消灭这段）。
# 注意 read_only 提示：告诉模型该工具无副作用（Claude Code 用它决定能否并行）。

TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文本文件内容。超长文件会保留头尾并标注省略行数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径，相对当前工作目录"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出指定目录的内容，每行带 [目录]/[文件] 标记。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，默认当前目录"},
                },
                "required": [],
            },
        },
    },
]


# ========== 4. LLM 调用（兼容性兜底都集中在这里） ==========

def chat(messages: list[dict]) -> dict:
    """发一次补全请求，返回 assistant 消息（裸 dict）。

    兜底清单（不同 OpenAI 兼容网关的真实差异）：
    - tool_calls 可能为 None（该模型没调工具）        -> 统一成 []
    - arguments 是 JSON *字符串* 而不是 dict           -> 解析放在调用处容错
    - 不传 parallel_tool_calls 参数                    -> 部分网关直接 400
    """
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=TOOLS_SCHEMA,
        extra_body=EXTRA_BODY,  # None 时 SDK 会忽略
    )
    msg = resp.choices[0].message
    # 还原成裸 dict（含 tool_calls），后面统一按 dict 处理
    m = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        m["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    # usage 打印（M3 会用它做"估算 vs 实际"对比，这里先建立直觉）
    if resp.usage:
        print(f"  [tokens] prompt={resp.usage.prompt_tokens} "
              f"completion={resp.usage.completion_tokens}")
    return m


# ========== 5. Agent Loop —— 整个项目的心脏 ==========

def build_system_prompt() -> str:
    """系统提示词组装：注入环境信息（Claude Code 也这么干——cwd/OS/git 状态注入）。"""
    return (
        "你是一个运行在终端里的编程助手 nano-agent（教学项目）。\n"
        "回答用中文，简洁。\n\n"
        "环境信息：\n"
        f"- 工作目录：{os.getcwd()}\n"
        f"- 操作系统：{platform.system()} {platform.release()}\n\n"
        "你可以调用工具（读文件、列目录）来了解项目后再回答。"
        "不确定的事情就先用工具确认，不要编造。"
    )


def run_turn(user_input: str, messages: list[dict]) -> str:
    """一次用户输入驱动的完整 while 循环，返回最终 assistant 文本。

    对照 LangGraph：下面的 while 就是 StateGraph 的节点循环，
    `if not tool_calls: break` 就是 conditional_edges 路由到 END。
    """
    messages.append({"role": "user", "content": user_input})

    for turn in range(1, MAX_TURNS + 1):
        # ---- 向模型发起请求 ----
        assistant = chat(messages)
        messages.append(assistant)  # add_messages 的全部真相

        tool_calls = assistant.get("tool_calls", [])
        if not tool_calls:  # 没有工具调用 => 本轮任务结束，这就是唯一的终止条件
            return assistant["content"]

        print(f"  [turn {turn}] 模型请求调用 {len(tool_calls)} 个工具：")

        # ---- 协议铁律 ----
        # 带 tool_calls 的 assistant 之后，必须按【原顺序】补齐每个 call 对应的
        # role=="tool" 消息（tool_call_id 对上），少一条或乱序都会被 API 拒绝（400）。
        for tc in tool_calls:
            name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"] or "{}"
            try:
                args = json.loads(raw_args)  # arguments 是 JSON 字符串，不是 dict
                if not isinstance(args, dict):
                    raise ValueError
            except (json.JSONDecodeError, ValueError):
                # 错误即观测（error as observation）：把错误文本作为工具结果
                # 返回给模型，它通常会自己修正——比抛异常打断循环优雅得多
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": f"[错误] arguments 不是合法 JSON：{raw_args}",
                })
                continue

            print(f"    -> {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})")

            func = TOOL_FUNCS.get(name)
            result = func(**args) if func else f"[错误] 未知工具：{name}"

            # 给终端看的回显截断（与给模型看的截断是两套阈值：人要可读，模型要完整）
            echo = result if len(result) <= 400 else result[:200] + f"\n...（回显截断，共 {len(result)} 字符）"
            print(f"    <- {echo}")

            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

    print(f"  [max_turns] 达到 {MAX_TURNS} 轮上限，强制结束（无限循环保护）")
    return "（已达到最大轮数，任务被强制中止）"


# ========== 6. REPL：交互式终端界面 ==========

def main() -> None:
    print("=" * 56)
    print(f"  nano-agent M1 | 模型：{MODEL}")
    print(f"  工具：{', '.join(TOOL_FUNCS)} | 输入 /exit 退出")
    print("=" * 56)

    # 消息历史跨轮持久——这就是 agent "记忆" 的全部真相：一个 list
    messages: list[dict] = [{"role": "system", "content": build_system_prompt()}]

    while True:
        try:
            user_input = input("\n你> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见。")
            break
        if not user_input:
            continue
        if user_input in ("/exit", "/quit"):
            print("再见。")
            break
        try:
            answer = run_turn(user_input, messages)
        except Exception as e:  # 网络/鉴权等错误在这里兜住，REPL 不崩
            print(f"\n[出错了] {e}\n（检查 .env 配置与网络后重试）")
            continue
        print(f"\nnano-agent> {answer}")


if __name__ == "__main__":
    main()
