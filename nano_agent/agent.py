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
                 context=None, permissions=None, indent: str = "",
                 bus=None) -> None:
        # context / permissions / bus 都是注入位：M3 上下文、M4 权限、观测层。
        # bus=None（终端 REPL）时埋点零开销；WebUI 注入 EventBus 实时推送。
        self.llm = llm
        self.tools = tools
        self.cfg = loop_cfg or LoopConfig()
        self.indent = indent  # M5：子代理的输出缩进（sidechain 可见性）
        self.context = context
        self.permissions = permissions
        self.bus = bus
        self._schema_tokens: int | None = None  # 工具定义的 token 缓存（观测用）
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]
        # 重复调用检测的状态
        self._last_sig: tuple | None = None
        self._sig_repeat = 0

    # ---------- 观测：集中一个小方法，埋点处一行搞定 ----------

    def _emit(self, event_type: str, **payload) -> None:
        if self.bus:
            # source 标记主/子代理：前端据此把子代理的轮次分流到⑥面板，
            # 不和主线的调度时间线混流（否则出现两个 turn 1）
            payload.setdefault("source", "sub" if self.indent else "main")
            self.bus.emit(event_type, **payload)

    # ---------- 主循环 ----------

    def run(self, user_input: str) -> str:
        """一次用户输入驱动的完整循环，返回最终 assistant 文本。"""
        self.messages.append({"role": "user", "content": user_input})

        for turn in range(1, self.cfg.max_turns + 1):
            # M3 钩子：请求前检查是否需要压缩上下文（M2 阶段 context=None，跳过）
            if self.context:
                self.context.maybe_compact()

            # 观测：本轮 prompt 的完整组装（WebUI 面板②的数据源）——
            # 每条消息一条 {role, preview, tokens}，外加工具定义的占比
            if self.bus:
                from .context import estimate_text
                if self._schema_tokens is None:
                    import json as _json
                    self._schema_tokens = estimate_text(
                        _json.dumps(self.tools.schema(), ensure_ascii=False))
                self._emit("prompt_assembly",
                           schema_tokens=self._schema_tokens,
                           total=self._schema_tokens + sum(
                               4 + estimate_text(m.get("content") or "")
                               for m in self.messages),
                           msgs=[{"role": m["role"],
                                  "preview": (m.get("content")
                                              or f"<{len(m.get('tool_calls', []))} 个工具调用>")[:80],
                                  "full": (m.get("content") or
                                           json.dumps(m.get("tool_calls", []),
                                                      ensure_ascii=False))[:4000],
                                  "tokens": 4 + estimate_text(m.get("content") or "")}
                                 for m in self.messages])
            self._emit("turn_start", turn=turn)

            assistant, usage = self.llm.chat(self.messages, self.tools.schema())
            self.messages.append(assistant)
            if self.context:  # M3 钩子：记录真实用量（估算 vs 实际对比）
                self.context.record_usage(usage)

            tool_calls = assistant.get("tool_calls", [])
            if not tool_calls:  # 唯一的"智能"终止条件
                # 带全文：前端把完整回答渲染进对话流（preview 字段保留兼容）
                self._emit("turn_end", turn=turn,
                           answer=assistant["content"],
                           answer_preview=assistant["content"][:120])
                return assistant["content"]

            print(f"{self.indent}{ui.DIM}[turn {turn}] {len(tool_calls)} 个工具调用{ui.RESET}")
            # 观测：模型响应（工具调用意图 + 用量）——注意包在 if 里：
            # 实参在调用前求值，bus=None 时不该为构造 payload 付出代价/踩 None
            if self.bus:
                u = usage or Usage()
                self._emit("llm_response",
                           thinking_preview=(assistant.get("_thinking") or "")[:400],
                           text_preview=(assistant["content"] or "")[:100],
                           tool_calls=[{"id": tc["id"], "name": tc["function"]["name"],
                                        "args": (tc["function"].get("arguments") or "")[:120]}
                                       for tc in tool_calls],
                           usage={"prompt": u.prompt_tokens,
                                  "completion": u.completion_tokens,
                                  "cached": u.cached_tokens})
            self._execute_calls(tool_calls)
            # 重复检测放在回填【之后】：纠偏的 user 消息如果插在
            # assistant(tool_calls) 和 tool 消息中间，会破坏协议配对顺序 -> 400
            self._check_repeat(tool_calls)

        print(f"{self.indent}{ui.YELLOW}[max_turns] 达到 {self.cfg.max_turns} 轮上限，强制结束{ui.RESET}")
        return "（已达到最大轮数，任务被强制中止）"

    # ---------- 工具调度：本层真正的机制 ----------

    def _execute_calls(self, tool_calls: list[dict]) -> None:
        """调度规则（对标 Claude Code 的真实设计，面试高频考点）：

        只读工具（read_only=True，无副作用、结果互不依赖）-> 可并行
        写工具（read_only=False，可能改文件/有副作用）      -> 必须串行
        权限 ASK 的调用也归入串行组——交互确认只发生在主线程，
        并行线程里弹 input 会交叉打架（这是把权限检查前置到分组阶段的原因；
        DENY 的拒绝观测无副作用，在并行组里是安全的）。
        协议铁律：结果统一收集，最后按 tool_calls 的【原顺序】append——
        无论中途异常与否，finally 兜底保证每个 call 都有配对结果。
        """
        results: dict[int, str] = {}
        try:
            # 第一步：解析全部参数（失败的记错误观测，不进入调度）
            parsed: list[tuple[int, dict]] = []
            for i, tc in enumerate(tool_calls):
                raw = tc["function"].get("arguments") or "{}"
                try:
                    args = json.loads(raw)
                    if not isinstance(args, dict):
                        raise ValueError
                except (json.JSONDecodeError, ValueError):
                    results[i] = f"[错误] arguments 不是合法 JSON：{raw[:200]}"
                    continue
                parsed.append((i, args))

            # 第二步：分组（只读且非 ASK 才进并行组，其余全进串行组）
            args_of = {i: a for i, a in parsed}  # index -> 参数 dict
            read_idx, write_idx = [], []
            for i, args in parsed:
                name = tool_calls[i]["function"]["name"]
                spec = self.tools.get(name)
                decision = self.permissions.check(name, args) if self.permissions else None
                if spec is None or not spec.read_only or decision == Decision.ASK:
                    write_idx.append(i)  # 写工具 / 未知工具 / 需要确认的 -> 串行
                else:
                    read_idx.append(i)   # 只读且无需交互 -> 并行安全
            # 观测：调度分组结果（面板④：谁并行、谁串行、为什么）
            self._emit("schedule",
                       parallel=[{"name": tool_calls[i]["function"]["name"],
                                  "args": args_of[i]} for i in read_idx],
                       serial=[{"name": tool_calls[i]["function"]["name"],
                                "args": args_of[i]} for i in write_idx])

            # 第三步：只读且 >=2 个才值得开线程池，否则串行更省
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

            # 第四步：其余调用逐个串行（ASK 的交互确认也只发生在这里）
            for i in write_idx:
                results[i] = self._call_one(tool_calls[i], args_of[i])
        finally:
            # 第五步（协议兜底）：即使中途异常逃逸，也要给每个 call 补上
            # 配对结果——否则留下孤儿 assistant(tool_calls)，此后每轮请求
            # 都会被严格网关按 id 校验拒绝
            for i in range(len(tool_calls)):
                results.setdefault(i, "[错误] 执行中断（该工具未运行）")
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
            answered = None
            if decision == Decision.DENY:
                print(f"{self.indent}{ui.RED}  [权限拒绝] {name}({arg_str}){ui.RESET}")
                self._emit("permission", tool=name, args=args,
                           decision="deny", answered=None)
                return denied_observation(name, args)
            if decision == Decision.ASK:
                ans = ui.confirm(name, args)
                answered = ans
                self._emit("permission", tool=name, args=args,
                           decision="ask", answered=ans)
                if ans == "n":
                    return denied_observation(name, args)
                if ans == "a":
                    self.permissions.remember_allow(name, args)
            else:
                self._emit("permission", tool=name, args=args,
                           decision="allow", answered=None)

        if spec is None:
            print(f"{self.indent}{ui.RED}  -> {name}({arg_str}){ui.RESET}")
            return f"[错误] 未知工具：{name}"
        print(f"{self.indent}  -> {name}({arg_str})")
        try:
            result = spec.func(**args)
        except Exception as e:  # 工具内部炸了也不打断循环
            result = f"[错误] 工具执行异常：{type(e).__name__}: {e}"
        print(f"{self.indent}{ui.DIM}  <- {ui.truncate_for_display(result)}{ui.RESET}")

        # 观测：工具完成（面板④时间线）+ 沙箱副作用（面板⑤）
        self._emit("tool_result", name=name, args=args,
                   preview=result[:200], full=result[:4000],
                   ok=not result.startswith(("[错误]", "[权限拒绝]")))
        if name in ("write_file", "edit_file") and not result.startswith(("[错误]", "[权限拒绝]")):
            self._emit("file_change", op=name, path=str(args.get("path", "")),
                       preview=result[:100])
        elif name == "bash":
            self._emit("bash_exec", command=str(args.get("command", ""))[:120],
                       exit_line=result.splitlines()[0] if result else "",
                       preview=result[:200])
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
