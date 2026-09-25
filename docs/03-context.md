# 03. 上下文管理与压缩详解

> 对应代码：`git diff m2-tools m3-context`。对照官方机制：
> "Manage context / auto-compact" 一节与 agent-loop 文档的 "What consumes context" 表。

## 一、上下文里到底有什么

官方文档把上下文消耗分列（我们用估算器复现了这张表）：

```
+----------------------------------------+
| system prompt（角色+环境注入）           |  ~250 tokens
| 工具定义（tools schema，每请求都发！）   |  ~890 tokens（7 个工具）
| 消息历史（user/assistant/tool 交替）    |  随对话增长
+----------------------------------------+
```

**实测教训**：估算只算消息历史时，与 API 实际偏差 +269%；把工具定义计入后
偏差降到 -3% ~ -9%。工具越多，schema 占比越大——这就是为什么工具要做
截断、要按需加载（官方的 tool-search 机制就是干这个的）。

## 二、字符级 token 估算（不用 tiktoken）

```python
def estimate_text(s: str) -> int:
    cjk = len(_CJK.findall(s))          # 中文 ≈ 1 token/字
    other = len(s) - cjk                # 英文/代码 ≈ 4 字符/token
    return math.ceil(cjk + other / 4)
```

为什么够用：a) 不同模型 tokenizer 本来就不同，精确没有意义；b) 触发阈值
只需要数量级正确（80% 预算线，±10% 误差不影响决策）；c) 每轮的
`[tokens] 估算 x | API 实际 y（偏差 z%）` 日志会让偏差持续可见——
这是把"不可观测的上下文"变成可观测的最小手段。

BPE 的背景：token 不是字也不是词，是字节对编码的高频片段。英文代码里
`def `、`import ` 这类片段是一个 token；中文字符几乎一字一个 token。
4:1 和 1:1 是这两个极端的经验近似。

## 三、压缩算法（auto-compaction）

### 触发

每次向 LLM 发请求前检查：`估算 > 预算 × 80%` → 压缩。
（留 20% 余量：本轮请求本身还会增长。`/compact` 手动触发走同一函数。）

### 保留什么

```
压缩前：[system, u1, a1(tc), t1, a1', u2, a2(tc), t2, a2', ... uN, ...]
                      \______ 被压缩段 ______/ \___ 保留段 ___/

压缩后：[system, [compact_boundary]+摘要, uK, aK(tc), tK, aK', ... uN, ...]
```

- `system` 永远保留（角色定义不能丢）
- 最近 `keep_recent=6` 条保留（当前任务的"工作集"）
- 中间段交给模型摘要，注入为一条带 `[compact_boundary]` 标记的 user 消息

### 安全边界：find_safe_boundary()（本机制最大的坑）

协议约束：带 tool_calls 的 assistant 之后必须紧跟配对 tool 消息。
如果切分点落在 (assistant, tool) 对中间，保留段开头就是孤儿 tool 消息——
下一轮请求直接 400。

解法：**从后往前数完 keep_recent 条后，继续向前扫，跳过 tool 消息，
停在第一条 user 上**。保留段起点永远是 user，被压缩段里
assistant(tool_calls) 与其 tool 结果同生共死。

```
... a(tc), t, a, | u, ...      <- 正确：边界落在 user 前
... a(tc) | t, a, u, ...       <- 错误：t 成了孤儿，400
```

（`tests/test_compact.py::test_boundary_never_orphans_tool_message`
用任意 keep_recent 验证了这一点。）

### 摘要 prompt 的设计

```
保留：(1) 用户原始目标 (2) 读写过的文件路径 (3) 跑过的命令与关键结果
     (4) 重要决策与发现 (5) 未完成事项。500 token 以内。
```

这五项是"换个脑子继续干活"所需的最小信息集。对照官方：Claude Code 的
压缩会参考 CLAUDE.md 的指导决定保留什么（可配置），同样是"摘要质量
决定压缩后能力下限"的问题。

## 四、原地替换的 Python 细节

```python
self.messages[:] = [system, boundary_msg, *self.messages[boundary:]]
#            ^^ 切片赋值：改的是 list 的内容，不是绑定
```

Agent、ContextManager、/history 命令都持有同一个 list 的引用。
`messages = [...]`（不带冒号）只改本地变量绑定，别的引用还指着旧列表
——压缩就静默失效了。这是 Python 教学里的经典坑，在这个场景里
有了真实的杀伤力。

## 五、动手验收清单

- [ ] 每轮看 `[tokens] 估算 x | API 实际 y（偏差 z%）`，正常范围 ±20%
- [ ] `/tokens` 看报告：历史 + 工具定义分列，压缩计数
- [ ] `/compact` 手动压缩（历史太短会提示"没有可压缩的历史"）
- [ ] 把 `TOKEN_LIMIT` 调到 1500 左右，多聊几轮触发自动压缩，
      然后问"我们最开始聊了什么"——模型应能凭摘要回答
      （注：模型质量决定效果，弱模型可能答不好，这与机制无关）

## 六、这一层在面试里怎么说

> "我的上下文估算是字符级的：中文 1:1、英文 4:1，关键是把工具定义也计入
> ——实测不计入会低估 2.7 倍。压缩在预算 80% 触发，切分点必须落在 user
> 消息上，否则会产生孤儿 tool 消息导致协议 400——这是我单测覆盖的重点。
> 替换用切片赋值原地改，因为 Agent 和 ContextManager 共享同一个 list 引用。"
