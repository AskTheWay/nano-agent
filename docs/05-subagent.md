# 05. 子代理详解：上下文隔离（sidechain）

> 对应代码：`git diff m4-permissions m5-subagent`。对照官方机制：
> Sub-agents 文档的"独立上下文窗口"一节。

## 一、要解决什么问题

让主 agent"调研一下这个项目里所有用了装饰器的地方"。不派子代理的走法：

```
主上下文： glob(**/*.py) -> 200 行路径
           grep(@)       -> 50 条匹配（每条一行上下文）
           read_file x10 -> 每个文件 2-8k tokens
           ...主上下文被 ~30k tokens 的中间过程淹没
```

派子代理的走法：

```
主上下文： task("调研所有装饰器用法") 
             └── 子上下文（独立）：glob/grep/read 随便造，用完即弃
           <- "调研摘要：共 12 处，集中在 tools/ 目录..."（几百 tokens）
```

**子代理 = 上下文沙箱**。脏活在沙箱里干，外面只留结论。这就是 Claude Code
官方说的"separate context window"；日志里的缩进显示就是 sidechain 的可见性设计。

## 二、三道闸（缺一不可）

```python
sub = Agent(llm, sub_tools, spec["system_prompt"], ..., permissions=permissions, indent="    ")
```

| 闸 | 实现 | 防什么 |
|---|---|---|
| 1. 只回传摘要 | run_task 返回格式化字符串；子 messages 是局部变量，函数结束即丢弃 | 中间过程污染主上下文 |
| 2. 工具子集无 task | `tools.subset(["read_file","glob","grep"])` | 子代理派孙代理 -> 无限递归烧钱 |
| 3. 复用主权限引擎 | `permissions=permissions` | 子代理绕过 deny 规则 |

第 2 道闸是"结构性防御"的教科书案例：不是在 task 工具里写
`if depth > 1: 拒绝`，而是让子代理的注册表里**根本不存在** task——
想犯规都没有门。对比运行时检查，结构性防御不依赖任何条件判断的正确性。

## 三、实现：一个工具 + 一个工厂 + 十行核心

- `subagent.py`：`SUBAGENT_TYPES`（类型表：system prompt + 工具子集 + 轮数预算）
  和 `run_task()`（new Agent + run + 统计格式化）。
- `tools/task.py`：`install(llm, permissions)` 工厂——task 的执行体需要
  llm/权限这两个只在 main 组装时才存在的依赖，闭包进去再注册。
  这也是为什么 task 不走 `@tool` 的 import 注册：**注册时机由依赖决定**。

扩展一个新子代理类型 = 往 `SUBAGENT_TYPES` 加一个条目（比如 writer：
允许 edit_file/write_file，system prompt 强调先读后改）。官方的内建类型
（general-purpose / Plan / Explore）就是同一思想的完整版。

## 四、子代理与主 agent 的生命周期对比

| | 主 agent | 子代理 |
|---|---|---|
| messages | REPL 全程共享一个 list | 每次 task 新建，用完丢弃 |
| 上下文管理 | ContextManager（压缩） | 无（轮数预算 10 轮内自然有界） |
| 终端显示 | 顶格 | 缩进两格（indent 参数） |
| 终止 | 用户 /exit | 任务完成或 10 轮上限 |

注意子代理**没有配 ContextManager**：它是有界的（max_turns=10），
压缩机制留给可能无限长的主会话——机制配给跟着生命周期走。

## 五、动手验收清单

- [ ] `用 task 派一个子代理调研：这个项目有哪些工具、哪些标记了只读` →
      观察缩进的子代理内部调用流；完成后主上下文只多一条 tool 消息
- [ ] `/tokens` 对比派子代理前后：主上下文增量应该只有几百 tokens
      （子代理自己烧掉的几千 tokens 不在主上下文里）
- [ ] `/history 20`：主 messages 里找不到子代理的 read_file/grep 记录

## 六、这一层在面试里怎么说

> "子代理的核心是上下文隔离：task 工具派生一个全新 messages 的子循环，
  只把最终摘要回传主上下文，中间的 tool 消息永不泄漏——我用 /tokens
  验证过主上下文增量只有几百 token。防递归用的是结构性防御：子代理的
  工具注册表是主表的子集，且不含 task 本身，所以它物理上派不了孙代理。
  权限引擎是共享的，子代理不能绕过 deny 规则。"
