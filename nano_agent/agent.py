"""Agent：while 循环 + 工具调度（只读并行 / 写串行）。

对应 M1 单文件里的"第 5 段：Agent Loop"，拆出来后成为永久的心脏。
M3 的 ContextManager、M4 的 PermissionEngine 都会以依赖注入的方式
插进这个类的预留位——这就是"机制可插拔"的最小体现。
"""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .llm import LLMClient, Usage
from .permissions import Decision, denied_observation
from .tools.registry import ToolRegistry
from . import ui


# ========== 循环行为配置 ==========

@dataclass
class LoopConfig:
    max_turns: int = 25            # 防无限循环第一道闸
    max_parallel_reads: int = 4    # 只读工具并行的最大并发
    repeat_limit: int = 3          # 连续 N 次完全相同的调用集 -> 注入纠偏提示


class Agent:
    """一个 agent = system prompt + 消息历史 + 调度器。

    self.messages 跨轮持久——"记忆"的全部真相依然是一个 list。
    """

    def __init__(self, llm: LLMClient, tools: ToolRegistry,
                 system_prompt: str, loop_cfg: LoopConfig | None = None,
                 context=None, permissions=None, indent: str = "") -> None:
        # context / permissions 是 M3/M4 的注入位，M2 阶段保持 None
        self.llm = llm
        self.tools = tools
        self.cfg = loop_cfg or LoopConfig()
        self.indent = indent  # M5：子代理的输出缩进（sidechain 可见性）
        self.context = context
        self.permissions = permissions
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]
        # 重复调用检测的状态
        self._last_sig: tuple | None = None
        self._sig_repeat = 0

    # ---------- 主循环 ----------

    def run(self, user_input: str) -> str:
        """一次用户输入驱动的完整循环，返回最终 assistant 文本。"""
        self.messages.append({"role": "user", "content": user_input})

        for turn in range(1, self.cfg.max_turns + 1):
            # M3 钩子：请求前检查是否需要压缩上下文（M2 阶段 context=None，跳过）
            if self.context:
                self.context.maybe_compact()

            assistant, usage = self.llm.chat(self.messages, self.tools.schema())
            self.messages.append(assistant)
            if self.context:  # M3 钩子：记录真实用量（估算 vs 实际对比）
                self.context.record_usage(usage)

            tool_calls = assistant.get("tool_calls", [])
            if not tool_calls:  # 唯一的"智能"终止条件
                return assistant["content"]

            print(f"{self.indent}{ui.DIM}[turn {turn}] {len(tool_calls)} 个工具调用{ui.RESET}")
            self._check_repeat(tool_calls)
            self._execute_calls(tool_calls)

        print(f"{self.indent}{ui.YELLOW}[max_turns] 达到 {self.cfg.max_turns} 轮上限，强制结束{ui.RESET}")
        return "（已达到最大轮数，任务被强制中止）"

    # ---------- 工具调度：本层真正的机制 ----------

    def _execute_calls(self, tool_calls: list[dict]) -> None:
        """调度规则（对标 Claude Code 的真实设计，面试高频考点）：

        只读工具（read_only=True，无副作用、结果互不依赖）-> 可并行
        写工具（read_only=False，可能改文件/有副作用）      -> 必须串行
        协议铁律：无论实际完成顺序如何，结果必须按 tool_calls 的【原顺序】append。
        """
        # 第一步：解析全部参数（解析失败的先给错误观测，不进入调度）
        parsed: list[tuple[int, dict | None]] = []
        for i, tc in enumerate(tool_calls):
            raw = tc["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw)
                if not isinstance(args, dict):
                    raise ValueError
            except (json.JSONDecodeError, ValueError):
                parsed.append((i, None))
                self._append_result(tool_calls[i],
                                    f"[错误] arguments 不是合法 JSON：{raw[:200]}")
                continue
            parsed.append((i, args))

        # 第二步：分组（M4 起，权限 ASK/DENY 的调用也归入串行组——
        # 交互确认只发生在主线程，并行线程里弹 input 会交叉打架。
        # 所以只读且权限放行的才进并行组，其余全进串行组。）
        args_of = {i: a for i, a in parsed}  # index -> 参数 dict
        read_idx, write_idx = [], []
        for i, args in parsed:
            if args is None:
                continue
            name = tool_calls[i]["function"]["name"]
            spec = self.tools.get(name)
            decision = self.permissions.check(name, args) if self.permissions else None
            if spec is None or not spec.read_only or decision == Decision.ASK:
                write_idx.append(i)  # 写工具 / 未知工具 / 需要确认的 -> 串行
            else:
                read_idx.append(i)   # 只读且放行 -> 并行安全

        # 第三步：只读且 >=2 个才值得开线程池，否则串行更省
        results: dict[int, str] = {}
        if len(read_idx) >= 2:
            print(f"{self.indent}{ui.CYAN}[并行] {len(read_idx)} 个只读工具同时执行{ui.RESET}")
            # with 块退出时会等全部任务完成（shutdown(wait=True)），随后取 result 安全
            with ThreadPoolExecutor(max_workers=self.cfg.max_parallel_reads) as pool:
                futures = {i: pool.submit(self._call_one, tool_calls[i], args_of[i])
                           for i in read_idx}
                results.update({i: fut.result() for i, fut in futures.items()})
        else:
            for i in read_idx:
                results[i] = self._call_one(tool_calls[i], args_of[i])

        # 第四步：写工具逐个串行（顺序执行，一个完成才做下一个）
        for i in write_idx:
            results[i] = self._call_one(tool_calls[i], args_of[i])

        # 第五步：按原顺序回填（协议铁律：乱序会被严格网关按 id 校验拒绝）
        for i in sorted(results):
            self._append_result(tool_calls[i], results[i])

    def _spec_of(self, tc: dict):
        return self.tools.get(tc["function"]["name"])

    def _call_one(self, tc: dict, args: dict) -> str:
        """执行单个工具调用：权限检查 -> 未知工具/执行异常都转成错误观测文本。"""
        name = tc["function"]["name"]
        spec = self.tools.get(name)
        arg_str = ", ".join(f"{k}={v!r}" for k, v in args.items())

        # M4：执行前的权限闸门（分组阶段已保证 ASK 只出现在串行路径）
        if self.permissions:
            decision = self.permissions.check(name, args)
            if decision == Decision.DENY:
                print(f"{self.indent}{ui.RED}  [权限拒绝] {name}({arg_str}){ui.RESET}")
                return denied_observation(name, args)
            if decision == Decision.ASK:
                ans = ui.confirm(name, args)
                if ans == "n":
                    return denied_observation(name, args)
                if ans == "a":
                    self.permissions.remember_allow(name, args)

        if spec is None:
            print(f"{self.indent}{ui.RED}  -> {name}({arg_str}){ui.RESET}")
            return f"[错误] 未知工具：{name}"
        print(f"{self.indent}  -> {name}({arg_str})")
        try:
            result = spec.func(**args)
        except Exception as e:  # 工具内部炸了也不打断循环
            result = f"[错误] 工具执行异常：{type(e).__name__}: {e}"
        print(f"{self.indent}{ui.DIM}  <- {ui.truncate_for_display(result)}{ui.RESET}")
        return result

    def _append_result(self, tc: dict, result: str) -> None:
        self.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

    # ---------- 无限循环第二道闸：重复调用检测 ----------

    def _check_repeat(self, tool_calls: list[dict]) -> None:
        """连续 N 轮发起【完全相同】的调用集 -> 注入一条纠偏提示。

        比直接 break 温和：给模型一次"被点名"的机会自己换路。
        """
        sig = tuple(sorted((tc["function"]["name"],
                            tc["function"].get("arguments") or "")
                           for tc in tool_calls))
        if sig == self._last_sig:
            self._sig_repeat += 1
        else:
            self._last_sig, self._sig_repeat = sig, 1
        if self._sig_repeat >= self.cfg.repeat_limit:
            self._sig_repeat = 0
            self.messages.append({
                "role": "user",
                "content": ("[系统提示] 检测到你连续多轮发起了完全相同的工具调用且未见效。"
                            "请换一种做法，或明确告诉用户目前遇到的困难。"),
            })
            print(f"{self.indent}{ui.YELLOW}[repeat] 连续 {self.cfg.repeat_limit} 次相同调用，已注入纠偏提示{ui.RESET}")
