# 08. Provider 层详解：多厂商适配（适配器模式）

> 对应代码：`nano_agent/providers/`。这是新增的一层，建议对照
> `git log` 里 provider 相关 commit 阅读本文。

## 一、要解决的问题

M1~M5 的 LLM 层只认一种协议（OpenAI chat.completions）。要接 Anthropic 原生
协议（messages API），有两种做法：

- **做法 A（坏）**：在循环层到处写 `if provider == "anthropic"`——
  协议差异渗进 Agent/调度/压缩，每加一家厂商全项目改一遍
- **做法 B（好）**：定一种**内部规范格式**，每家厂商写一个适配器做
  **双向转换**——循环层永远只认规范格式，加厂商 = 加一个文件

做法 B 就是适配器模式（Adapter Pattern）。本项目选它。

## 二、分层图

```
Agent / 调度器 / 压缩 / 权限            ← 完全不感知厂商（零改动！）
      │ 只认"规范消息格式"（OpenAI 风格裸 dict）
      ▼
BaseProvider.chat(messages, tools)     ← 统一契约（providers/base.py）
  ├── OpenAIProvider                    规范格式 == 自家协议，直传零转换
  └── AnthropicProvider                 to_anthropic() / from_anthropic() 双向转换
```

**为什么规范格式选 OpenAI 风格**：它是事实标准——智谱/DeepSeek/mimo/
OpenRouter 全是它的兼容方言，覆盖面最大；绝大多数用户走 OpenAIProvider
零转换开销。Anthropic 的差异集中在一个文件里，出问题只怀疑一处。

## 三、两家协议的差异清单（anthropic_provider.py 的全部工作）

| 维度 | 规范格式（OpenAI 风格） | Anthropic messages |
|---|---|---|
| system | `messages[0]` 的一条 | **顶层字段**，不在 messages 里 |
| content | 永远是字符串 | **块数组**（text / tool_use / tool_result…） |
| 工具调用 | `tool_calls: [{id, function: {name, arguments(JSON字符串)}}]` | assistant content 里的 `{type:"tool_use", id, name, input: dict}` |
| 工具结果 | 独立的 `role:"tool"` 消息 | user 消息里的 `tool_result` 块；**同一轮多个结果必须合并进同一条 user 消息** |
| 工具定义 | `{type:"function", function:{name, description, parameters}}` | `{name, description, input_schema}`（拆掉 function 包装） |
| 输出上限 | 可选 | `max_tokens` **必填** |
| 缓存字段 | `prompt_tokens_details.cached_tokens` | `cache_read_input_tokens`（+`cache_creation`） |

最容易踩的坑：**连续 tool 消息的合并**——OpenAI 协议里每条 tool 结果是独立
消息，Anthropic 要求同一轮的工具结果在同一条 user 消息里。转换函数里
"看前一条是不是 tool_result 块，是则 append"的十几行就是干这个的
（`tests/test_providers.py::test_consecutive_tools_merge_into_one_user_message`
验证了双工具场景）。

## 四、工厂与注册表

```python
PROVIDERS = {"openai": OpenAIProvider}      # 类注册表（providers/__init__.py）

def get_provider(cfg):
    name = cfg.provider or "auto"
    if name == "auto":
        name = "anthropic" if "anthropic" in cfg.base_url.lower() else "openai"
    ...
```

对照 M2 的工具注册表——同一个"注册 + 工厂"把戏，区别是工具注册的是
**实例**（装饰器副作用），provider 注册的是**类**（构造需要 cfg）。
`auto` 模式按 base_url 猜协议，让大多数用户完全无感。

## 五、"连接池"的真相（面试高频误解）

**不要自己造连接池**。openai / anthropic SDK 的底层都是 `httpx.Client`：
内建连接池（TCP/TLS 连接 keep-alive 复用、并发连接上限、健康检查）。
provider 层要做的"池管理"只有一件事——**单例复用**：

```python
class OpenAIProvider(BaseProvider):
    def __init__(self, cfg):
        self._client = OpenAI(...)   # 一个 provider 实例持有一个池化 client

    def chat(self, ...):
        self._client.chat.completions.create(...)  # 复用，绝不在请求里新建
```

错误示范是每次 `chat()` 里 `OpenAI(...)` 新建客户端——连接永远无法复用，
TLS 握手开销每次都付。本项目 main/webui 组装时 `create_client(cfg)` 调一次，
Agent 终身持有，这就是全部。

## 六、怎么用

`.env` 配置（或 WebUI ⚙ 弹窗下拉选）：

```ini
# 官方 Anthropic
OPENAI_BASE_URL=https://api.anthropic.com
OPENAI_API_KEY=sk-ant-xxx
MODEL_NAME=claude-sonnet-5
PROVIDER=anthropic

# Z.AI 的 Anthropic 协议网关
OPENAI_BASE_URL=https://api.z.ai/api/anthropic
MODEL_NAME=glm-5.3
PROVIDER=auto        # base_url 含 "anthropic" 自动识别，显式写也行
```

依赖：`pip install anthropic`（可选——只用 OpenAI 兼容端点可不装，
装了也只在选中 anthropic 分支时才 import）。

## 七、扩展练习

1. 加一个 `GeminiProvider`：读 Google 的 generateContent 协议文档，
   写 `to_gemini/from_gemini` 转换 + 注册表登记——工作量参考 Anthropic
   的 ~150 行，主要差异在 content 的 parts 结构
2. Anthropic 的 `cache_control` 显式缓存标记：给 system/tools 打标后
   `cache_read_input_tokens` 才会有值（本教学版未做——注意 OpenAI 是自动
   缓存、Anthropic 要显式标记，这是两家缓存策略的本质差异）
3. 重试/退避策略：SDK 自带 429 重试（`max_retries` 参数），可以在 provider
   层统一配置暴露给 .env

## 八、这一层在面试里怎么说

> "我用了适配器模式做多厂商接入：内部统一 OpenAI 风格的裸 dict 消息格式
> （事实标准、兼容网关覆盖面最大），Anthropic 适配器做双向协议转换——
> 最大的坑是它要求同一轮的多个工具结果合并进同一条 user 消息的
> tool_result 块，还有 system 提升为顶层字段。连接管理上我没有自建池：
> SDK 底层 httpx 自带连接池，provider 只保证客户端单例复用。
> 新增厂商 = 一个 BaseProvider 子类 + 注册表一行，循环层零改动。"
