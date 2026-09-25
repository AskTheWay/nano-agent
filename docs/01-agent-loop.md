# 01. Agent Loop 详解：图不过是一个 while 循环

> 对应代码：`git checkout m1-loop` 后看 `nano_agent/main.py`（单文件 ~250 行）。
> 对照官方机制：code.claude.com/docs 的 "How Claude Code works" 与 Agent SDK "agent-loop" 页。

## 一、先看全景：一次对话在循环里发生了什么

```
 你> main.py 里的 while 循环从第几行开始？
   │
   ▼
 messages.append(user)                     ┐
   │                                       │
   ▼                                       │
 ┌───────────── while 循环 ─────────────┐  │
 │  chat(messages) ──> assistant 消息    │  │  这就是 StateGraph
 │    │                                 │  │  的全部运行时
 │    ├─ 无 tool_calls ──> break ───────┼──┼──> 返回文本给你
 │    │                                 │  │
 │    └─ 有 tool_calls                  │  │
 │         for tc in tool_calls:        │  │
 │           执行工具，结果 append 为    │  │
 │           role=="tool" 消息          │  │
 └─────────────────────────────────────┘  │
   （messages 跨轮持久 = agent 的"记忆"）  ┘
```

真实运行时终端会打印（ DEBUG 观测是理解循环的最好工具）：

```
你> main.py 里的 while 循环从第几行开始？
  [tokens] prompt=780 completion=45
  [turn 1] 模型请求调用 1 个工具：
    -> read_file(path='nano_agent/main.py')
    <- (文件内容回显...)
  [tokens] prompt=3200 completion=60
nano-agent> main.py 第 150 行 ...
```

两轮 LLM 请求、一次工具调用——**这中间没有图、没有节点、没有边，只有一个 while**。

## 二、消息协议：四种 role 的接力

agent 的全部状态就是一个 `list[dict]`（LangChain 的消息类只是它们的糖衣）：

```python
[
 {"role": "system",    "content": "你是 nano-agent...（含 cwd/OS 注入）"},
 {"role": "user",      "content": "main.py 里的 while 从第几行开始？"},
 {"role": "assistant", "content": "", "tool_calls": [
     {"id": "call_abc", "type": "function",
      "function": {"name": "read_file",
                   "arguments": "{\"path\": \"nano_agent/main.py\"}"}}]},
 {"role": "tool", "tool_call_id": "call_abc", "content": "（文件全文...）"},
 {"role": "assistant", "content": "main.py 第 150 行 ..."},   # 最终回答
]
```

逐字段拆解（容易踩坑的都标了）：

| 字段 | 说明 | 坑 |
|---|---|---|
| `tool_calls[].function.arguments` | 参数是 **JSON 字符串**，不是 dict | 必须 `json.loads`，且可能非法 |
| `tool_calls[].id` | 本次调用的唯一标识 | 回填 tool 消息时 `tool_call_id` 必须对上 |
| 消息顺序 | assistant(tool_calls) 后必须**按原顺序**紧跟全部对应 tool 消息 | 少一条/乱序 → API 直接 400 |
| `content` | assistant 在发起工具调用的同一轮通常为空字符串 | 别当异常处理 |

## 三、和你写过的 react_demo.py 逐行对照

| react_demo.py（LangGraph） | m1 的 main.py（裸实现） |
|---|---|
| `StateGraph(AgentState)` | `run_turn()` 里的 `for turn in range(...)` 循环 |
| `graph.add_node("agent", agent_node)` | `chat(messages)` 一次调用 |
| `graph.add_node("tools", tool_node)` | `for tc in tool_calls:` 执行段 |
| `should_continue` 路由函数 | `if not tool_calls: break` 一行 |
| `state["iterations"]` 防无限循环 | `MAX_TURNS=25`（同一个思想） |
| `@tool` + `bind_tools` | 手写 `TOOLS_SCHEMA` dict（m2 消灭它） |
| `AIMessage / ToolMessage` 构造 | 两个 dict 字面量 |

**ReAct 的 TAO（Thought-Action-Observation）在协议里怎么体现？**
现代模型不再靠提示词玩 "Thought: ... Action: ..." 文本游戏（2022 年原版 ReAct 论文的方式），
而是：Thought = assistant 的隐藏推理，Action = 结构化的 `tool_calls` 字段，
Observation = `role=="tool"` 消息内容。你的 `react_demo.py` 系统提示词里那套
"Thought/Action/Observation 格式要求"在新协议下已经是多余的了——可以对比删掉后的效果。

## 四、终止条件与循环保护

1. **正常终止**：模型不再发起工具调用（`tool_calls` 为空）→ 它认为任务完成。
   这是唯一的"智能"终止信号。
2. **强制终止**：`MAX_TURNS=25` 轮上限。防两类事故：模型反复调用同一工具不收敛；
   工具一直报错模型一直重试。
3. **M2 会加第三道**：连续 3 次"同名工具 + 相同参数"检测（刷屏检测）。

## 五、错误即观测（error as observation）

工具执行失败时，**不要抛异常打断循环**，把错误文本作为 tool 消息返回：

```
模型 ──read_file(不存在.py)──> 工具
模型 <── "[错误] 读取失败：... No such file ..."    ← 错误就是观测结果
模型 ──list_dir('.')──> 工具                        ← 自己改道重试
```

m1 的代码里有两处体现：文件读取失败返回 `[错误]` 文本；`arguments` JSON
解析失败也返回错误文本。这比异常中断优雅得多——模型天然会"看着错误换路走"，
而且这是 Claude Code 官方文档明确描述的行为。

## 六、system prompt 也是机制

注意 `build_system_prompt()`：它注入了**工作目录、操作系统**等环境信息。
Claude Code 的做法（官方文档 "How Claude Code works" 一节）还包括 git 状态、
目录树快照等。能力的一半在工具，另一半在 prompt 的信息供给——
模型只有知道"自己在哪"才能正确使用相对路径。

## 七、动手验收清单

```bash
git checkout m1-loop
pip install -r requirements.txt
copy .env.example .env        # 填好三个变量
python -m nano_agent
```

- [ ] 问 "main.py 里的 while 循环从第几行开始" → 观察 turn 1 工具调用、turn 2 文本回答
- [ ] 问 "你好" → 无工具调用，单轮直接回答（对照：终止条件生效）
- [ ] 故意把 .env 的 key 改错 → 启动或首问时得到友好报错而非 traceback
- [ ] 把 MAX_TURNS 改成 2，给它一个刁钻任务 → 观察 2 轮后强制结束

## 八、这一层在面试里怎么说

> "我不用框架手写过 agent 循环：消息历史就是 OpenAI 协议的裸 dict 列表，
> LangGraph 的图等价于一个 while 循环，bind_tools 等价于 schema 生成。
> 我踩过 tool_calls 回填顺序的坑——带工具调用的 assistant 后必须按原序补齐
> 全部 tool 消息，否则 400。"
