"""Provider 抽象基类：定义所有厂商适配器必须遵守的契约。

接口只有两个方法——刻意的窄接口（ISP：只暴露循环层需要的最小能力）：

    chat(messages, tools) -> (assistant 消息 dict, Usage)

契约细则（子类必须保证，否则循环层会炸）：
1. 入参 messages 是 OpenAI 风格的裸 dict 列表（规范格式），子类负责转成自家协议；
2. 返回的 assistant 消息也是规范格式：
   - 无工具调用：{"role": "assistant", "content": "文本"}
   - 有工具调用：额外带 "tool_calls": [{id, type, function: {name, arguments}}]，
     其中 arguments 必须是【JSON 字符串】（解析容错在 agent.py）；
3. Usage 的 cached_tokens 尽力提取（各家字段不同，见 llm.Usage 文档）；
4. 任何厂商侧错误直接抛异常——REPL/WebUI 有兜底，provider 不吞错。
"""

from abc import ABC, abstractmethod


class BaseProvider(ABC):
    """厂商适配器的统一契约。"""

    #: 供观测/日志用的展示名
    name: str = "base"

    @abstractmethod
    def chat(self, messages: list[dict], tools: list[dict] | None = None
             ) -> tuple[dict, "Usage"]:
        """一次补全请求：规范格式进，规范格式出。"""

    # 说明：Usage 定义在 nano_agent.llm（避免循环 import，历史上它就在那）。
    # 类型注解用字符串引用，运行时不解析。
