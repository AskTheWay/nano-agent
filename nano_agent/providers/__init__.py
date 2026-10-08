"""多厂商 provider 层：适配器模式（Adapter Pattern）的教学实现。

架构图：

    Agent / 调度器 / 压缩 / 权限          <- 完全不感知厂商差异
          │ 只认一种"规范消息格式"
          ▼
    BaseProvider.chat(messages, tools)    <- 统一接口（本文件定义契约）
      ├── OpenAIProvider                   chat.completions 协议（含一切兼容网关）
      └── AnthropicProvider                messages 协议（system 独立 / 块状 content）

为什么规范格式选 OpenAI 风格的裸 dict：
1. 它是事实上的行业标准——智谱/DeepSeek/mimo/OpenRouter 全是它的兼容方言，
   覆盖面最大，绝大多数场景走 OpenAIProvider 零转换；
2. Anthropic 的差异（system 顶层字段、tool_use/tool_result 块）集中在
   转换函数里，出问题时只需要怀疑一个文件。

关于"连接池"的真相：
openai / anthropic SDK 的底层都是 httpx.Client——内建连接池（keep-alive 复用、
并发上限）。provider 要做的池管理是【单例复用】：一个 provider 实例持有一个
client，进程内不重复建连。自己再造连接池是对分层的误解。
"""

from .base import BaseProvider
from .openai_provider import OpenAIProvider

# 注册表：新增厂商 = 写一个 BaseProvider 子类 + 在这里登记一行。
# 对照 M2 工具注册表——同样是"注册 + 工厂"的把戏，但这里是类注册不是实例注册。
PROVIDERS: dict[str, type] = {
    "openai": OpenAIProvider,
}


def get_provider(cfg) -> BaseProvider:
    """工厂：按配置选择并构造 provider。

    PROVIDER 环境变量取值：
      - "openai" / "anthropic"   显式指定
      - "auto"（默认）            按 base_url 猜（含 "anthropic" 字样 -> anthropic）
    """
    # 延迟 import：anthropic SDK 是可选依赖，没装时只在使用该分支才报错
    name = (getattr(cfg, "provider", None) or "auto").lower()
    if name == "auto":
        name = "anthropic" if "anthropic" in cfg.base_url.lower() else "openai"
    if name == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(cfg)
    cls = PROVIDERS.get(name)
    if cls is None:
        raise ValueError(f"未知 provider：{name}（可选：{', '.join(PROVIDERS)}）")
    return cls(cfg)
