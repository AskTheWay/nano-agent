# 06. 附录：OpenAI function calling 协议详解 + 网关实测差异

> 这一篇是查漏补缺的参考篇：协议字段逐个讲清 + 换模型端点时的实测坑。

## 一、一次带工具的请求长什么样

```jsonc
// 请求体（POST /v1/chat/completions）
{
  "model": "glm-4.7-flash",
  "messages": [
    {"role": "system",    "content": "你是 nano-agent..."},
    {"role": "user",      "content": "main.py 的 while 在第几行？"},
    {"role": "assistant", "content": "",
     "tool_calls": [
       {"id": "call_abc", "type": "function",
        "function": {"name": "read_file",
                     "arguments": "{\"path\": \"nano_agent/main.py\"}"}}]},
    {"role": "tool", "tool_call_id": "call_abc", "content": "（文件内容）"}
  ],
  "tools": [
    {"type": "function",
     "function": {"name": "read_file", "description": "...",
                  "parameters": {"type": "object", "properties": {...},
                                 "required": [...]}}}
  ]
}
```

```jsonc
// 响应里的 assistant 消息（模型决定调工具时）
{"role": "assistant", "content": null,
 "tool_calls": [{"id": "call_abc", "type": "function",
                 "function": {"name": "read_file",
                              "arguments": "{\"path\": \"nano_agent/main.py\"}"}}]}
```

### 字段速查

| 字段 | 是什么 | 容易踩的坑 |
|---|---|---|
| `tools[].function.parameters` | JSON Schema | 别用 `strict`（OpenAI 专属，兼容端 400） |
| `tool_calls[].id` | 本次调用唯一标识 | 回填 tool 消息时 `tool_call_id` 必须一字不差 |
| `tool_calls[].function.arguments` | **JSON 字符串** | 不是 dict！必须 `json.loads`，且可能非法 |
| 消息顺序 | assistant(tc) 后紧跟配对 tool 消息 | 少一条/乱序 -> 400（严格网关按 id 校验） |
| `parallel_tool_calls` | 允许一轮多个调用 | 我们**不传**：部分兼容网关对显式传 false 也 400 |
| `tool_calls` 为 null | 模型本轮不调工具 | 统一成 `[]` 处理（llm.py 的兜底） |

## 二、各家 OpenAI 兼容端点实测差异表（2026-09 实测）

| 端点 | function calling | 实测发现 |
|---|---|---|
| 智谱 `open.bigmodel.cn/api/paas/v4` (glm-4.7-flash) | ✓ 正常 | 免费 flash 档限流狠（连续请求易 429，code 1305）；长上下文里塞满 tool 消息时注意力明显退化（会把完整历史当"第一次对话"）；usage 计数正常 |
| 小米 mimo `token-plan-cn.xiaomimimo.com/v1` | ✓ | 需 `extra_body={"thinking":{"type":"disabled"}}`（走 OPENAI_EXTRA_BODY 配）；端点偶发 TLS 中断；key 有过期/轮换情况 |
| OpenRouter `openrouter.ai/api/v1` | ✓ | 大陆可达性看网络；模型名带前缀（如 `anthropic/claude-sonnet-5`）；充值有 5.5% 手续费 |

> 换端点三步：改 `.env` 三个变量 → 跑 `python -m nano_agent` 问一句 →
> 观察 `[tokens] 估算 vs API 实际` 偏差是否在 ±20% 内（估算器按中英文
> 字符比例工作，极端语种会偏）。

## 三、为什么不做 streaming

流式（SSE）把一个 response 拆成几十个 chunk，工具调用要自己拼装增量
的 arguments 片段——复杂度全在协议拼装上，对"理解 agent 机制"没有增量。
教学版选择非流式：一次请求一个完整 response，循环逻辑一目了然。
（想挑战的话：`stream=True` + 按 `delta` 拼 `tool_calls.arguments`，
是很好的练习题。）

## 四、估算器校准记录（M3 的实测数据）

| 配置 | 偏差 | 原因 |
|---|---|---|
| 只算消息历史 | **+269%** | 漏了 tools schema（~750 tokens，6 个工具） |
| 历史 + 工具定义 | **-3% ~ -9%** | 正确口径 |

教训（对应官方 agent-loop 文档的 "What consumes context" 表）：
上下文 = system prompt + **工具定义** + 消息历史 +（skill 描述等）。
只算看得见的消息会严重低估——工具越多，schema 占比越大。

## 五、本项目刻意不做的（和为什么）

| 不做 | 原因 |
|---|---|
| streaming | 见第三节 |
| MCP | 传输层协议（stdio/SSE）与 agent 机制正交，demo/mcp 已有样例 |
| 多模态 | 图片消息结构，对循环机制无增量 |
| 持久化会话 | 官方是 JSONL append + resume；教学版内存即可（很好的扩展练习） |
| strict schema / parallel_tool_calls 参数 | 兼容网关会拒 |
