# nano-agent

几百行 Python，看懂 AI Agent 的全部核心机制。

对标 Claude Code 的五层核心机制，**0 框架依赖**（不用 LangChain / LangGraph / pydantic）：

```
M1 Agent Loop      —— LangGraph 的图，不过是一个 while 循环
M2 工具调度        —— bind_tools 藏的其实是 JSON schema + tool_calls 协议
M3 上下文压缩      —— token 估算 + auto-compaction，长会话不至于失忆
M4 权限系统        —— deny-first，写操作必须过 y/n 确认
M5 子代理          —— 隔离上下文的子 loop，只回传摘要（sidechain 思想）
```

> No framework. Just a loop.

---

## ⚠️ 安全声明（先读这个）

- 本项目的 `bash` / `write_file` 工具本质是**任意代码执行**，内置权限系统（M4）只是教学演示，
  不要用它跑不可信的任务、不要在生产环境使用。
- 本项目与 Anthropic / Claude 官方没有任何关系，"对标 Claude Code" 仅指学习其公开文档
  （code.claude.com/docs）中描述的机制设计，不包含任何泄露源码。
- API key 一律走 `.env`（已加入 `.gitignore`），绝不硬编码、绝不提交。

## 快速开始

```bash
pip install -r requirements.txt
copy .env.example .env      # 填入你的 OPENAI_BASE_URL / OPENAI_API_KEY / MODEL_NAME
python -m nano_agent
```

任意 OpenAI 兼容端点都可以：小米 mimo、智谱、DeepSeek、OpenRouter、官方 API，
换个 `OPENAI_BASE_URL` 就行。

## 学习路线（tag 导览）

每个 tag 都是一个**完整可运行**的状态，`git diff` 相邻 tag 就是该机制的增量：

| tag | 机制 | 代码量 | 配套文档 |
|---|---|---|---|
| `m0-init` | 项目骨架与安全基线 | — | — |
| `m1-loop` | 最小 agent loop（手写 schema） | ~230 行 | docs/01-agent-loop.md |
| `m2-tools` | 工具注册表 + coding 工具集 + 只读并行调度 | ~600 行 | docs/02-tools.md |
| `m3-context` | token 估算 + 自动压缩 | ~750 行 | docs/03-context.md |
| `m4-permissions` | deny-first 权限系统 | ~870 行 | docs/04-permissions.md |
| `m5-subagent` | 子代理上下文隔离 | ~1000 行 | docs/05-subagent.md |

```bash
git checkout m1-loop          # 回到最小循环，看着它长大
git diff m1-loop m2-tools     # 看工具系统是怎么加上去的
```

> README 会随每一层推进持续更新，学习路线与面试叙事在 M5 完成后补全。
