# nano-agent

> 几百行 Python，看懂 AI Agent 的全部核心机制。
>
> **No framework. Just a loop.**

用 ~1400 行裸 Python（0 框架依赖，不依赖 LangChain / LangGraph / pydantic，
只有 `openai` SDK）重新实现 Claude Code 的五层核心机制。每一层是一个 git tag，
**每个 tag 都完整可运行**——适合 demo 教学、源码精读、面试准备。

```
M1 Agent Loop      —— LangGraph 的图，不过是一个 while 循环
M2 工具调度        —— bind_tools 藏的其实是 JSON schema + tool_calls 协议
M3 上下文压缩      —— token 估算 + auto-compaction，长会话不至于失忆
M4 权限系统        —— deny-first，写操作必须过 y/n 确认
M5 子代理          —— 隔离上下文的子 loop，只回传摘要（sidechain 思想）
```

---

## ⚠️ 安全声明（先读这个）

- 本项目的 `bash` / `write_file` 工具本质是**任意代码执行**，内置权限系统（M4）只是教学演示，
  不要用它跑不可信的任务、不要在生产环境使用。
- 本项目与 Anthropic / Claude 官方没有任何关系，"对标 Claude Code" 仅指学习其公开文档
  （code.claude.com/docs）中描述的机制设计，不包含任何泄露源码。
- API key 一律走 `.env`（已加入 `.gitignore`），绝不硬编码、绝不提交。

## 架构一图流

```
你 ──► main.py（REPL；/tools /tokens /compact 等斜杠命令本地拦截，不进模型）
          │
          ▼
    Agent.run() —— 一个 while 循环（M1）
          │
          ├──► M3 ContextManager：请求前估算 token，超预算 80% 自动压缩
          │
          ├──► LLMClient.chat() ──► 任意 OpenAI 兼容端点
          │        ▲ tools.schema() 由 @tool 装饰器反射生成（M2）
          │
          ├── 模型不再发起 tool_calls？ ──► 返回文本，本轮结束（唯一的自然终止条件）
          │
          └── 发起 tool_calls？ ──► M2 调度器 _execute_calls()
                    ├── M4 PermissionEngine.check() 先分组：deny 即死 / ask 归入串行
                    ├── 只读且免确认 ──► 线程池并行执行
                    ├── 写 / 需确认   ──► 串行（y/n/a 交互只发生在这里）
                    └── 结果按 tool_calls 原序回填为 tool 消息 ──► 下一轮

    M5 task 工具 ──► 派生子 Agent：全新消息历史（上下文沙箱），只回传摘要
```

"记忆"的全部真相：`self.messages` 就是一个 `list[dict]`，跨轮持久。

## 快速开始

前置要求：**Python 3.10+**（用到了 `X | None` 原生联合类型语法）、任意
OpenAI 兼容 API（智谱 / DeepSeek / 小米 mimo / OpenRouter / 官方均可）。

```bash
pip install -r requirements.txt
copy .env.example .env      # Windows；macOS/Linux 用 cp。填 OPENAI_BASE_URL / OPENAI_API_KEY / MODEL_NAME
python -m nano_agent
```

换个端点只是换个 `OPENAI_BASE_URL`（各端点实测差异见 docs/06）。

**WebUI 观测台**（可选，学习向的可视化面板）：

```bash
pip install fastapi uvicorn
python -m nano_agent.webui     # 打开 http://127.0.0.1:8765
```

六个面板实时展示每轮 prompt 的组装（system+schema+消息的 token 堆叠条）、
调度分组与权限判定、上下文仪表盘、沙箱文件副作用、子代理 sidechain——
详见 [docs/07-webui.md](docs/07-webui.md)。

测试（全部不触网，改代码后先跑它）：

```bash
python -m pytest tests/ -q    # 63 个单测
```

## 现场演示脚本（demo 教学用）

按顺序敲，每一步恰好点亮一层机制。建议对着 [架构一图流](#架构一图流) 讲。

| # | 你输入 | 你会看到 | 背后机制 | 讲解要点 |
|---|---|---|---|---|
| 1 | `同时读 README.md 和 requirements.txt，各用一句话总结` | `[并行] 2 个只读工具同时执行` | M2 | 只读无副作用 → 线程池并行；结果仍按原序回填（协议铁律） |
| 2 | `创建 sandbox/hello.py 写一个打印函数并运行它` | 弹出 `[权限确认] y/n/a`，输入 `y` | M4 | default=ask 兜底写操作；`a` = 本会话同类放行 |
| 3 | `读一下 .env` | 直接 `[权限拒绝]`，不询问 | M4 | deny-first：命中即死，不给确认机会（安全优先于便利） |
| 4 | `/permissions` | allow/deny/会话级规则清单 | M4 | 规则语法 `tool(specifier)`，如 `bash(git diff:*)` 前缀匹配 |
| 5 | `用 task 派一个子代理调研这个项目的工具注册情况` | 缩进的 `[task]` 子代理输出 | M5 | 子代理全新上下文，脏活在内、结论在外；工具子集防递归 |
| 6 | `/tokens` | 估算 vs API 实际的偏差、缓存命中率 | M3 | 工具定义也占上下文；缓存命中是 agent 成本关键杠杆 |
| 7 | `/compact` | `压缩 N 条历史 -> 摘要 1 条` | M3 | 压缩必须找 user 边界，防孤儿 tool 消息（协议约束决定算法） |
| 8 | `/history 20` | 裸 dict 消息历史着色渲染 | M1 | system/user/assistant/tool 四种角色的全部真相 |
| 9 | 任务跑偏时按 `Ctrl+C` | `已中止本轮任务，继续对话` | — | Ctrl+C 语义是"中止本轮"而非退出 REPL |

演示前的准备：确认 `.env` 可用、建议在项目目录里跑（权限规则按 cwd 的
`permissions.json` 加载）、第 2 步会真实写文件（用 sandbox/ 目录隔离）。

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

先读 [docs/00-learning-path.md](docs/00-learning-path.md)：里面有
LangGraph 概念 → 裸实现的映射表（你已经会的那套 → 这里的一百行）。

## 代码地图（demo 时直接跳转）

| 机制 | 文件 | 行数 | 核心入口 |
|---|---|---|---|
| REPL + 斜杠命令 | `nano_agent/main.py` | 166 | `main()` / `build_system_prompt()` |
| **M1** while 循环 | `nano_agent/agent.py` | 198 | `Agent.run()` |
| **M2** 调度器 | `nano_agent/agent.py` | 198 | `_execute_calls()`（分组 → 并行/串行 → 原序回填） |
| **M2** schema 反射生成 | `nano_agent/tools/registry.py` | 133 | `build_schema()` / `@tool` 装饰器 |
| **M2** 工具实现 ×7 | `nano_agent/tools/{fs,search,shell,task}.py` | 287 | read_file / write_file / edit_file / glob / grep / bash / task |
| **M3** 估算 + 压缩 | `nano_agent/context.py` | 182 | `estimate_messages()` / `find_safe_boundary()` / `maybe_compact()` |
| **M4** 规则引擎 | `nano_agent/permissions.py` | 150 | `Rule.matches()` / `PermissionEngine.check()` |
| **M5** 子代理 | `nano_agent/subagent.py` | 67 | `run_task()`（三道闸都在这 67 行里） |
| 网关差异兜底 | `nano_agent/llm.py` | 95 | `LLMClient.chat()` / `Usage` |
| 配置 / 终端 UI | `nano_agent/config.py` `ui.py` | 115 | `load_config()` / `confirm()` |

总量：源码 ~1400 行（含中文注释）/ 测试 ~800 行 63 个单测 / 详解文档 7 篇。

## 这个项目证明你理解什么（面试速查）

| 层 | 一句话 |
|---|---|
| M1 | 消息历史是裸 dict，图是 while，bind_tools 是 schema 生成；tool_calls 回填有序否则 400 |
| M2 | inspect+Annotated 反射生成 schema；只读并行/写串行，结果按原序回填 |
| M3 | 上下文=消息+工具定义；压缩要找 user 边界防孤儿 tool 消息；估算偏差实测 -5% |
| M4 | deny-first 三态判定；ASK 交互前置到调度分组（多线程不能抢 stdin）；拒绝即观测 |
| M5 | 子代理=上下文沙箱，只回传摘要；工具子集防递归是结构性防御；权限共享防绕过 |

每篇文档末尾的"面试里怎么说"段落是这些要点的展开版。

## 常见问题（FAQ）

- **Windows 终端中文乱码 / 没颜色？** 入口的 `ui.setup_console()` 已做 UTF-8 重配置
  和 VT 唤醒；仍异常就换 Windows Terminal。颜色在重定向到管道时自动关闭。
- **模型一直不调工具？** 确认模型支持 function calling，且 `.env` 三个必填项齐全；
  建议先用 `/tools` 确认工具注册成功。
- **`read_file` 读大文件被截断？** 设计如此：给模型的是头 100 行 + 尾 20 行，
  给终端回显的是另一套更紧的阈值（`ui.py` 开头注释讲了为什么是两套）。
- **`/tokens` 的估算和 API 实际差不少？** ±10% 以内属正常（没有 tiktoken，
  字符级经验估算对触发阈值足够）；每轮的偏差日志就是用来建立直觉的。
- **改了 `.env` 不生效？** `load_dotenv()` 不覆盖已存在的环境变量；
  已 export 过的旧值优先，必要时先清掉再启动。

## 边界（刻意不做的）

streaming / MCP / 多模态 / 会话持久化 / strict schema——每一条的取舍理由见
[docs/06-openai-protocol.md](docs/06-openai-protocol.md) 第五节。
它们都是不错的扩展练习。
