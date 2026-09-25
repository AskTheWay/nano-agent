"""权限系统：deny-first 规则引擎 + y/n/a 交互确认。

对标 Claude Code 的权限模型（官方 Permissions/IAM 文档）：
- deny 规则先查，命中即死，不给确认机会（安全优先于便利）
- allow 规则次查，命中静默放行
- 都没命中 -> 走 default（ask：终端弹 y/n/a 确认）
- "a(lways)" 追加会话级 allow 规则（官方是写入 settings.json 持久化，我们简化为会话级）

规则语法致敬官方的 Tool(specifier) 写法：

    "read_file"                工具名精确匹配（任意参数）
    "read_*"                   工具名通配
    "bash(git status)"         bash 命令整串相等
    "bash(git diff:*)"         bash 命令前缀匹配（:*) 结尾
    "read_file(**/.env)"       路径类参数 glob 匹配（fnmatch，正斜杠归一）
"""

import fnmatch
import json
import re
from enum import Enum

from . import ui


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"   # 交给终端交互确认（agent 的串行执行路径处理）


# ========== 规则解析与匹配 ==========

_RULE_RE = re.compile(r"^([\w?*]+)(?:\((.*)\))?$")


class Rule:
    """一条权限规则：工具名（可通配） + 可选的参数限定。"""

    def __init__(self, raw: str) -> None:
        self.raw = raw
        m = _RULE_RE.match(raw.strip())
        if not m:
            raise ValueError(f"规则语法错误：{raw!r}（应为 tool 或 tool(specifier)）")
        self.tool, self.spec = m.group(1), m.group(2)

    def matches(self, tool_name: str, args: dict) -> bool:
        # 第一关：工具名（fnmatch 支持 read_* 这类尾通配）
        if not fnmatch.fnmatch(tool_name, self.tool):
            return False
        if self.spec is None:
            return True  # 无参数限定 -> 该工具的任何调用都匹配

        if tool_name == "bash":
            cmd = str(args.get("command", "")).strip()
            if self.spec.endswith(":*"):  # 前缀匹配：git diff:* -> git diff / git diff HEAD
                prefix = self.spec[:-2].strip()
                return cmd == prefix or cmd.startswith(prefix + " ")
            return cmd == self.spec.strip()  # 整串相等

        # 其余工具：把 spec 当路径 glob，匹配第一个"路径类"参数
        # （path / pattern / dir 哪个在用哪个——够用即止的约定）
        for key in ("path", "pattern", "dir"):
            if key in args:
                norm = str(args[key]).replace("\\", "/")  # Windows 反斜杠归一
                return _path_match(norm, self.spec)
        return False


def _path_match(path: str, pattern: str) -> bool:
    """路径 glob 匹配。

    坑：fnmatch 把 ** 当普通 * 处理（还会跨 /），所以 "**/.env" 匹配不了
    根目录下的 ".env"（它要求至少一个 /）。手工补一个"去掉 **/ 前缀"的
    零层变体，凑出 glob 的跨层语义。
    """
    if fnmatch.fnmatch(path, pattern):
        return True
    return pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:])


# ========== 引擎 ==========

class PermissionEngine:
    """check() 是纯函数（无交互）：调度器在分组阶段调用它决定并行/串行归属，
    交互确认只发生在串行执行路径里——避免多线程弹窗竞争。
    """

    def __init__(self, config_path: str | None = None) -> None:
        self.allow: list[Rule] = []
        self.deny: list[Rule] = []
        self.default = Decision.ASK
        self.session_allow: list[Rule] = []  # "a" 追加的会话级规则
        if config_path:
            self._load(config_path)

    def _load(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"{ui.YELLOW}[权限] 配置读取失败（{e}），按最保守模式运行{ui.RESET}")
            return
        self.allow = [Rule(r) for r in cfg.get("allow", [])]
        self.deny = [Rule(r) for r in cfg.get("deny", [])]
        self.default = Decision(cfg.get("default", "ask"))

    # ---------- 判定 ----------

    def check(self, tool_name: str, args: dict) -> Decision:
        """deny-first：顺序即优先级。"""
        for r in self.deny:               # 1. deny 命中即死，不询问
            if r.matches(tool_name, args):
                return Decision.DENY
        for r in self.allow + self.session_allow:  # 2. allow（配置 + 会话级）
            if r.matches(tool_name, args):
                return Decision.ALLOW
        return self.default               # 3. 默认 ask（写操作主要靠它兜底）

    def remember_allow(self, tool_name: str, args: dict) -> None:
        """"a(lways)" 的落地：追加会话级规则。

        官方做法是写进 settings.json 持久化；教学版只保会话级——
        重启后重新问一遍，更安全也更简单。
        """
        if tool_name == "bash":
            cmd = str(args.get("command", "")).strip()
            first = cmd.split(" ")[0] if cmd else ""
            self.session_allow.append(Rule(f"bash({first}:*)"))  # 按首 token 放行
        else:
            self.session_allow.append(Rule(tool_name))  # 该工具本会话全放行

    def describe(self) -> str:
        out = [f"allow: {[r.raw for r in self.allow]}",
               f"deny:  {[r.raw for r in self.deny]}",
               f"default: {self.default.value}",
               f"会话级放行: {[r.raw for r in self.session_allow] or '（无）'}"]
        return "\n".join(out)


def denied_observation(tool_name: str, args: dict) -> str:
    """拒绝时返回给模型的 observation 文本（不是异常——错误即观测，模型自我改道）。"""
    brief = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in list(args.items())[:3])
    return (f"[权限拒绝] {tool_name}({brief}) 未被允许执行。"
            f"请换一个不需要该操作的方式完成任务，或向用户说明需要授权。")
