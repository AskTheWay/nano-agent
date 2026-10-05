"""上下文管理：token 估算 + auto-compaction（自动压缩）。

对标 Claude Code 的 auto-compact 机制（官方 "Manage context" 一节）：
上下文接近预算时，把旧历史交给模型摘要，保留 system + 摘要 + 最近几轮，
原地替换消息列表——长会话不至于"失忆"，token 预算不爆。

为什么不用 tiktoken：a) 少一个依赖；b) 不同模型的 tokenizer 本来就不同，
"够用的估算"（误差 ±10%）对触发阈值完全足够。每轮的
"估算 vs API 实际"对比日志会让你对误差有直觉。
"""

import json
import math
import re

from . import ui
from .llm import LLMClient

# 中日韩统一表意文字 + 中文标点 + 全角符号
_CJK = re.compile(r"[一-鿿　-〿＀-￯]")

# 每条消息固定的协议包装开销（role 名、结构字符等的粗略模拟）
_MSG_OVERHEAD = 4


# ========== token 估算（字符级） ==========

def estimate_text(s: str) -> int:
    """中文 ≈ 1 token/字；英文/代码 ≈ 4 字符/token（BPE 的经验值）。"""
    cjk = len(_CJK.findall(s))
    other = len(s) - cjk
    return math.ceil(cjk + other / 4)


def estimate_messages(msgs: list[dict]) -> int:
    """整段历史的 token 估算。注意 tool_calls 的 arguments 也占 token。"""
    total = 0
    for m in msgs:
        total += _MSG_OVERHEAD
        total += estimate_text(m.get("content") or "")
        if m.get("tool_calls"):
            total += estimate_text(json.dumps(m["tool_calls"], ensure_ascii=False))
    return total


# ========== 压缩安全边界（本机制最大的坑，单独成函数） ==========

def find_safe_boundary(messages: list[dict], keep_recent: int = 6) -> int:
    """返回保留段的起点 index（messages[boundary:] 保留，可压缩 messages[1:boundary]）。

    协议约束决定了算法：带 tool_calls 的 assistant 之后必须紧跟配对的
    tool 消息。所以保留段的起点【必须是 user 消息】——
    从后往前数 keep_recent 条，再继续向前扫，跳过 tool 消息，
    停在第一条 user 上。这样被压缩段里 assistant(tool_calls) 与其
    tool 结果"同生共死"，不会产生孤儿。

    注意返回 1 的含义：boundary=1 时被压缩段是 messages[1:1] = 空——
    即"历史太短/结构不允许，一条也压不掉"，调用方要处理这种情况。
    """
    i = len(messages) - keep_recent
    i = max(i, 1)  # 永不动 system（index 0）
    while i > 1 and messages[i].get("role") != "user":
        i -= 1
    return i


# ========== ContextManager：Agent 的注入组件 ==========

class ContextManager:
    """与 Agent 共享同一个 messages list 引用（压缩时原地替换）。"""

    def __init__(self, messages: list[dict], llm: LLMClient, token_limit: int,
                 bus=None) -> None:
        self.messages = messages
        self.llm = llm
        self.token_limit = token_limit
        self.bus = bus  # 观测注入位（同 Agent 的 bus，WebUI 模式才装）
        # 工具定义也是上下文！每次请求 tools schema 都随消息一起发送，
        # 占比可观（官方 agent-loop 文档的 "What consumes context" 表把
        # system prompt / 工具定义 / 历史分列——这里同样计入，否则估算严重偏低）
        self.schema_tokens = 0
        # 统计
        self.est_last = 0            # 最近一次请求前的估算
        self.api_prompt_total = 0    # 累计真实 prompt tokens（API 返回）
        self.api_completion_total = 0
        self.cached_total = 0        # 累计命中前缀缓存的 prompt tokens
        self.compact_count = 0
        # 测试钩子：可注入假摘要函数（不触网）
        self.summarizer = None       # Callable[[str], str] | None

    def set_schema_tokens(self, tools_schema: list[dict]) -> None:
        """把工具定义的 token 计入预算（main 组装时调用一次）。"""
        self.schema_tokens = estimate_text(json.dumps(tools_schema, ensure_ascii=False))

    # ---------- 统计 ----------

    def record_usage(self, usage) -> None:
        """Agent 每轮 chat 后调用（见 agent.py 的钩子）。

        顺带打印"估算 vs API 实际"对比——对估算误差建立直觉的地方。
        est_last 是本轮请求【前】的采样，与本次 API 返回的 prompt 同口径。
        """
        if self.est_last and usage.prompt_tokens:
            drift = (usage.prompt_tokens - self.est_last) / max(self.est_last, 1)
            cached_note = (f"，其中缓存命中 {usage.cached_tokens}" if usage.cached_tokens
                           else "")
            print(f"{ui.DIM}  [tokens] 估算 {self.est_last} | API 实际 "
                  f"{usage.prompt_tokens}（偏差 {drift:+.1%}{cached_note}）{ui.RESET}")
        self.api_prompt_total += usage.prompt_tokens
        self.api_completion_total += usage.completion_tokens
        self.cached_total += getattr(usage, "cached_tokens", 0)

    def report(self) -> str:
        # 缓存命中率：命中部分按约 0.1 倍计价，是 agent 成本的关键杠杆
        hit_rate = (f"{self.cached_total / self.api_prompt_total:.1%}"
                    if self.api_prompt_total else "n/a")
        return (f"当前估算：历史 {estimate_messages(self.messages)} + "
                f"工具定义 {self.schema_tokens} = {self.estimate()} tokens\n"
                f"累计 API 用量：prompt {self.api_prompt_total} + "
                f"completion {self.api_completion_total}\n"
                f"缓存命中：{self.cached_total} tokens（命中率 {hit_rate}；"
                f"0 = 端点未报告或无命中——智谱国内版不返回该字段）\n"
                f"上下文预算：{self.token_limit}（超过 80% 触发自动压缩）\n"
                f"已压缩次数：{self.compact_count}")

    # ---------- 压缩 ----------

    def estimate(self) -> int:
        """总估算 = 消息历史 + 工具定义（两者都是每次请求的 prompt 组成部分）。"""
        return estimate_messages(self.messages) + self.schema_tokens

    def maybe_compact(self) -> None:
        """Agent 每轮 chat 前调用：估算超预算 80% 就压缩。"""
        self.est_last = self.estimate()
        if self.est_last > self.token_limit * 0.8:
            self.compact_now()
            # 压缩后复检：保留窗口自身超预算时压缩已经救不了
            # （单条超大消息/预算过小），必须让用户看见而不是静默失效
            if self.estimate() > self.token_limit * 0.8:
                print(f"{ui.RED}[compact] 压缩后仍超预算——单条消息过大或预算过小，"
                      f"建议 /clear 或调大 TOKEN_LIMIT{ui.RESET}")

    def compact_now(self) -> str:
        """执行压缩并原地替换 messages（保持引用不断裂——Agent 持有同一个 list）。"""
        before = self.estimate()
        boundary = find_safe_boundary(self.messages)
        dropped = self.messages[1:boundary]  # system（index 0）永远保留
        if not dropped:
            return "（没有可压缩的历史：保留窗口已覆盖全部对话）"

        script = "\n".join(f"{m['role']}: {(m.get('content') or '')[:2000]}"
                           for m in dropped)
        summary = (self.summarizer or self._llm_summarize)(script)

        # 原地替换：messages[:] = ... 而不是 messages = ...
        # （后者只改本地绑定，Agent 里那个引用还指着旧列表——经典 Python 坑）
        self.messages[:] = [
            self.messages[0],  # system
            {"role": "user", "content":
                f"[compact_boundary]\n以上会话已压缩。此前对话摘要：\n{summary}"},
            *self.messages[boundary:],
        ]
        self.compact_count += 1
        after = self.estimate()
        msg = (f"[compact] 压缩 {len(dropped)} 条历史 -> 摘要 1 条 | "
               f"估算 {before} -> {after} tokens")
        print(f"{ui.YELLOW}  {msg}{ui.RESET}")
        if self.bus:  # 观测：压缩事件（面板③的触发标记）
            self.bus.emit("compact", before=before, after=after,
                          dropped=len(dropped), summary=summary[:120])
        return msg

    def _llm_summarize(self, script: str) -> str:
        """让模型自己摘要旧对话（不带 tools——这是纯粹的文本任务）。"""
        m, _ = self.llm.chat([{
            "role": "user",
            "content": (
                "Summarize the following conversation between a user and a "
                "coding agent. Preserve, as a compact bullet list: "
                "(1) the user's original goals, "
                "(2) files read/created/edited (with paths), "
                "(3) commands run and key results, "
                "(4) important decisions and findings, "
                "(5) anything unfinished. "
                "Keep it under 500 tokens. Conversation:\n" + script),
        }])
        return m.get("content") or "(摘要失败)"
