# 02. 工具系统详解：bind_tools 藏了什么

> 对应代码：`git diff m1-loop m2-tools`。对照官方机制：Tools reference（截断与约定）、
> agent-loop 文档里 "read-only 工具可并行" 的调度规则。

## 一、M1 -> M2 拆分映射表（先看这个再 diff）

M1 的单文件 main.py 按职责拆成了六个模块，diff 时对照这张表读：

| M1 单文件里的段落 | M2 的归属 |
|---|---|
| 第 1 段：环境与配置 | `config.py`（AppConfig + load_config） |
| 第 2/3 段：工具实现 + 手写 schema | `tools/fs.py` 等 + `tools/registry.py`（schema 自动生成） |
| 第 4 段：LLM 调用 | `llm.py`（LLMClient + Usage） |
| 第 5 段：while 循环 | `agent.py`（Agent 类 + 调度器） |
| 第 6 段：REPL | `main.py`（薄入口 + 斜杠命令） |
| 零散的 print | `ui.py`（颜色 + 两套截断） |

## 二、注册表：一段 inspect 反射干掉手写 schema

M1 里那段手写的 TOOLS_SCHEMA 有多痛苦（加一个工具要改两处、类型抄错没人提醒），
`@tool` 装饰器就有多香：

```python
@tool(read_only=True)
def read_file(path: Annotated[str, "文件路径，相对当前工作目录"],
              offset: Annotated[int, "起始行号，从 1 开始"] = 1,
              limit: Annotated[int, "最多读取的行数，0 表示读到末尾"] = 0) -> str:
    """读取文本文件内容，带行号前缀；超长时保留头尾并标注省略行数。"""
```

编译期信息只有三样：函数名、类型注解、docstring。`build_schema()` 用
`inspect.signature()` 反射签名，把这三样拼成协议要的 JSON schema：

```
函数名        -> function.name
docstring 首段 -> function.description
类型注解       -> parameters.properties[].type   （TYPE_MAP 映射表）
Annotated 描述 -> parameters.properties[].description
无默认值       -> parameters.required
```

**这就是 `bind_tools([read_file, ...])` 的全部秘密**——LangChain 在 import 你的函数时
做一模一样的反射（它支持 pydantic 是为了更复杂的校验，代价是引一串依赖）。

### 值得停下来想的取舍

注册靠 import 副作用（`tools/__init__.py` 里 `from . import fs`），代价是**全局可变
状态**：import 顺序错了 REGISTRY 就是空的，而且同一个进程里只能有一套全局工具表。
LangChain 的 `bind_tools([...])` 是显式传参，没有这个坑，但每个调用点都要维护列表。
两种都有真实项目在用——面试聊到这里就到位了。

## 三、调度器：只读并行 / 写串行

Claude Code 官方 agent-loop 文档明确描述了这个规则，原因值得逐条理解：

```
一次 tool_calls = [read a.py, read b.py, write c.py]
                    │                │
                    └──── 只读组 ────┘ 无副作用、结果互不依赖
                    ThreadPoolExecutor 并行执行
                                      │
                      写组（write c.py）── 等只读组全部完成后，逐个串行
```

1. **为什么只读能并行**：不修改任何状态，两个读操作的结果互不影响，
   并行只赚不亏（模型经常一次要看三五个文件）。
2. **为什么写必须串行**：写操作可能依赖前序工具的结果（先 write 配置再 bash 重启），
   并行会引入竞态。哪怕两个写互不相干，串行的代价也只是几十毫秒。
3. **协议铁律**：并行执行完成顺序是乱的，但回填必须按 `tool_calls` 的**原顺序**
   append——严格网关会按 id 逐条校验 assistant(tool_calls) 与 tool 消息的配对。
   实现上所有结果统一收集到一个 dict，最后一次性按序回填；`finally` 兜底
   保证即使中途异常，每个 call 也有配对结果（否则孤儿 assistant(tool_calls)
   会让之后每轮请求都被拒）。
   （`tests/test_truncate.py::test_tool_result_order_preserved` 用"故意的慢工具"
   端到端验证了这一点。）

模型侧的配合：部分模型单轮只回 1 个 call，调度器天然退化为串行——代码不需要
任何特殊处理（这就是"不传 parallel_tool_calls 参数"的原因之一）。

## 四、六个工具的设计细节

| 工具 | read_only | 对标 | 关键设计 |
|---|---|---|---|
| read_file | ✓ | Read | 行号前缀；>120 行头 100+尾 20 截断；offset/limit 分页 |
| write_file | ✗ | Write | 自动建父目录；整体覆盖 |
| edit_file | ✗ | Edit | **old_str 必须唯一匹配**，0 处/多处都报可行动的错误 |
| glob | ✓ | Glob | `**` 递归；跳过 .git/__pycache__ 等；上限 200 条 |
| grep | ✓ | Grep | 纯 Python 正则；上限 50 匹配；跳过 >1MB 文件 |
| bash | ✗ | Bash | 超时杀死；utf-8 解码；stdout 30k 截断；返回 exit code |

### 两套截断阈值（容易忽略的设计）

- **给模型的**：read_file 头 100+尾 20 行、bash stdout 30k 字符、grep 50 条——
  模型需要足够的上下文完整性，但刷屏输出会瞬间吃掉 token 预算。
- **给终端的**：`ui.truncate_for_display` 回显 ~400 字符——人只需要知道
  "发生了什么"，细节在 messages 里。

### edit_file 的报错是"可行动的"

```
[错误] old_str 在 main.py 中匹配到 3 处，不唯一。请在 old_str 中多带几行上下文，使其只匹配一处。
```

错误消息直接告诉模型下一步怎么做——配合"错误即观测"，模型通常一轮就自我修正。
对比一个只返回 "edit failed" 的实现，高下立判。

## 五、无限循环的三道闸（M2 集齐前两道）

1. `max_turns=25`：总量上限，最后防线。
2. **重复检测**：连续 3 轮发起完全相同的调用集 → 注入一条 user 纠偏提示
   （比直接 break 温和：给模型一次"被点名"后自己换路的机会）。
3. bash 超时：单命令级别的兜底。

## 六、system prompt 也是机制

M2 起 system prompt 里有了"工作规范"（改文件前先读、优先 grep 定位、报错按提示重试）。
这些规范和工具的报错文案是**配套设计**：工具报错引导行为，prompt 固化习惯。
Claude Code 能力的另一半就在这些细节里（官方文档同样把大量行为约束写在系统提示词中）。

## 七、动手验收清单

- [ ] "同时读 README.md、requirements.txt、.env.example 然后总结" → 观察一行
      `[并行] 3 个只读工具同时执行`，且结果回填顺序与请求一致
- [ ] "在 sandbox/ 创建 hello.py 写一个打印函数，然后运行它" → write_file 完成后
      才执行 bash（串行），运行结果正确
- [ ] "把 config.py 里的 30000 改成 50000" → edit_file 精确替换；故意让它匹配
      歧义文本（比如就替换一个 "："），观察它按报错提示自动扩大上下文重试
- [ ] "跑 python -c \"import time; time.sleep(100)\"" → 10 秒超时被杀
- [ ] `/tools` → 查看注册表（带只读/写标记）

## 八、这一层在面试里怎么说

> "我的工具注册表用 inspect + Annotated 从函数签名自动生成 JSON schema，
> 等价于 bind_tools 的反射部分。调度上实现了只读并行/写串行——只读工具无副作用
> 所以用线程池并行，但要按 tool_calls 原序回填结果，否则协议校验会 400。
> 我还做了连续相同调用的检测注入纠偏提示，这是防 agent 死循环比 max_turns
> 更细粒度的手段。"
