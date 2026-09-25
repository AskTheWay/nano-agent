# 00. 学习路线：五个 tag 看懂 Claude Code 的核心机制

> 前置阅读。这一篇回答三个问题：这个项目怎么学、每层机制对应什么面试考点、和你已会
> 的 LangGraph 是什么关系。

## 一、项目是什么

nano-agent 用 ~1000 行裸 Python（0 框架依赖，只有 `openai` SDK）重新实现 Claude Code
的五层核心机制。每一层是一个 git tag，**每个 tag 都完整可运行**：

```
m0-init ──> m1-loop ──> m2-tools ──> m3-context ──> m4-permissions ──> m5-subagent
 骨架        最小循环    工具+调度     上下文压缩      权限系统           子代理
```

学习方式：

```bash
git checkout m1-loop           # 从最小循环开始
python -m nano_agent           # 跑起来，玩一会儿
git diff m1-loop m2-tools      # 看这一层加了什么（增量即一节课）
```

## 二、LangGraph 概念映射表（你已经会的那套 → 这里的裸实现）

| LangGraph / LangChain 写法 | nano-agent 裸实现 | 在哪个 tag 能看到 |
|---|---|---|
| `StateGraph(AgentState)` 状态图 | 一个 `while` 循环 + 消息 `list[dict]` | m1 |
| `graph.add_conditional_edges(..., {END})` | `if not tool_calls: break` | m1 |
| `llm.bind_tools([search, ...])` | 手写 JSON schema dict → m2 的 `@tool` 装饰器自动生成 | m1→m2 |
| `HumanMessage / AIMessage / ToolMessage` | `{"role": "user"/"assistant"/"tool", ...}` 裸 dict | m1 |
| `add_messages` reducer | `messages.append(...)` | m1 |
| `tool_func.invoke(tc["args"])` | `func(**json.loads(tc["arguments"]))` | m1 |
| 框架内置的重试/异常包装 | 错误即观测（error as observation） | m1 |

**核心领悟**：agent 框架没有魔法。图是循环，消息是 dict，绑定工具是 schema 生成。
框架的价值在于工程化封装（重试、流式、并发、持久化），而不是机制本身。

## 三、每层机制的面试考点

| 层 | 机制 | 面试常问法 |
|---|---|---|
| M1 | agent loop / tool_calls 协议 | "手写一个不依赖框架的 agent 循环" |
| M2 | 工具注册 + 只读并行/写串行调度 | "为什么只读工具可以并行、写不行？" |
| M3 | token 估算 + auto-compaction | "长对话上下文爆了怎么办？" |
| M4 | deny-first 权限模型 | "coding agent 的安全边界怎么设计？" |
| M5 | 子代理上下文隔离（sidechain） | "多 agent 上下文怎么隔离/为什么要隔离？" |

## 四、文档索引

| 文档 | 主题 |
|---|---|
| [01-agent-loop.md](01-agent-loop.md) | 最小循环：while、tool_calls 协议、终止条件 |
| 02-tools.md | 工具注册表、schema 自动生成、并行调度（m2 解锁） |
| 03-context.md | token 估算、压缩算法与安全边界（m3 解锁） |
| 04-permissions.md | deny-first 规则引擎（m4 解锁） |
| 05-subagent.md | 上下文隔离的子循环（m5 解锁） |
| 06-openai-protocol.md | 附录：function calling 协议逐字段 + 各网关实测差异 |

## 五、参考的官方机制（合规声明）

所有机制对照均来自 Claude Code **公开官方文档**（code.claude.com/docs，大陆可直连）：

- How Claude Code works / Agent SDK 的 agent-loop 页（循环与消息类型）
- Tools reference（各工具的截断与约定）
- Manage context / auto-compact（压缩机制）
- Permissions (IAM) / Settings（权限规则语法）
- Sub-agents（独立上下文窗口）

本项目不含任何泄露源码，与 Anthropic 官方无关。
