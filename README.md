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

任意 OpenAI 兼容端点都可以：智谱、DeepSeek、小米 mimo、OpenRouter、官方 API，
换个 `OPENAI_BASE_URL` 就行（各端点实测差异见 docs/06）。

测试：`python -m pytest tests/ -q`（38 个单测，全部不触网）

## 学习路线（tag 导览）

每个 tag 都是一个**完整可运行**的状态，`git diff` 相邻 tag 就是该机制的增量：

| tag | 机制 | 新增代码 | 配套文档 |
|---|---|---|---|
| `m0-init` | 项目骨架与安全基线 | — | — |
| `m1-loop` | 最小 agent loop（手写 schema） | ~250 行 | [01-agent-loop.md](docs/01-agent-loop.md) |
| `m2-tools` | 工具注册表 + 工具集 + 只读并行调度 | +350 行 | [02-tools.md](docs/02-tools.md) |
| `m3-context` | token 估算（含工具定义）+ 自动压缩 | +150 行 | [03-context.md](docs/03-context.md) |
| `m4-permissions` | deny-first 权限系统 | +170 行 | [04-permissions.md](docs/04-permissions.md) |
| `m5-subagent` | 子代理上下文隔离 | +110 行 | [05-subagent.md](docs/05-subagent.md) |

```bash
git checkout m1-loop          # 回到最小循环，看着它长大
git diff m1-loop m2-tools     # 看工具系统是怎么加上去的
```

总量：源码 ~1280 行（含中文注释）/ 测试 ~430 行 / 详解文档 7 篇。

## 五分钟试一圈

```
你> 同时读 README.md 和 requirements.txt，分别总结核心内容     # <- 只读并行调度
你> 在 sandbox 里创建 hello.py 写个打印函数然后运行它           # <- 写串行 + bash
y                                                              # <- 权限确认
你> 帮我读一下 .env                                            # <- deny 直接拒绝
你> 用 task 派一个子代理调研这个项目的工具注册情况               # <- 子代理隔离
你> /tokens                                                    # <- 上下文用量
你> /permissions                                               # <- 权限规则
```

## 这个项目证明你理解什么（面试速查）

| 层 | 一句话 |
|---|---|
| M1 | 消息历史是裸 dict，图是 while，bind_tools 是 schema 生成；tool_calls 回填有序否则 400 |
| M2 | inspect+Annotated 反射生成 schema；只读并行/写串行，结果按原序回填 |
| M3 | 上下文=消息+工具定义；压缩要找 user 边界防孤儿 tool 消息；估算偏差实测 -5% |
| M4 | deny-first 三态判定；ASK 交互前置到调度分组（多线程不能抢 stdin）；拒绝即观测 |
| M5 | 子代理=上下文沙箱，只回传摘要；工具子集防递归是结构性防御；权限共享防绕过 |

每篇文档末尾的"面试里怎么说"段落是这些要点的展开版。

## 边界（刻意不做的）

streaming / MCP / 多模态 / 会话持久化 / strict schema——每一条的取舍理由见
[docs/06-openai-protocol.md](docs/06-openai-protocol.md) 第五节。
它们都是不错的扩展练习。
