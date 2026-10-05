"""事件总线：agent 的可观测性（observability）层。

对标官方 Agent SDK 的 observability 主题——生产 agent 必须能回答
"每一轮发了什么、为什么调这个工具、权限怎么判的、上下文花了多少"。

设计：Agent / ContextManager / 子代理都持有一个 bus 注入位（默认 None），
关键机制节点 emit 事件。bus=None 时零开销——终端 REPL 模式不装 bus，
WebUI 模式装一个把事件推给浏览器的实现。这就是"机制可插拔"的又一例：
观测不侵入逻辑，逻辑不依赖观测。
"""

import threading
from collections import defaultdict


class EventBus:
    """线程安全的事件总线。

    emit() 可从任意线程调用（工具在线程池里执行时也会发事件）；
    subscribe() 注册回调，异常自动吞掉（观测代码绝不能弄坏主流程）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[str, list] = defaultdict(list)
        self._wildcard: list = []  # 订阅全部事件的回调（WebUI 用）

    def subscribe(self, event_type: str, callback) -> None:
        """订阅某一类事件；event_type='*' 订阅全部。"""
        with self._lock:
            (self._wildcard if event_type == "*" else self._subs[event_type]).append(callback)

    def emit(self, event_type: str, **payload) -> None:
        """发布事件。回调里的异常吞掉并打印——观测是旁路，不是主干。"""
        with self._lock:
            cbs = list(self._subs.get(event_type, ())) + list(self._wildcard)
        for cb in cbs:
            try:
                cb(event_type, payload)
            except Exception as e:  # noqa: BLE001
                print(f"[bus] 观测回调异常（已忽略）：{e}")


# 事件清单（约定 type -> 关键 payload，前端按 type 路由渲染）：
#
# session_start   model, tools[], token_limit            会话建立
# turn_start      turn                                    每轮开始
# prompt_assembly msgs[{role,preview,tokens}],            发请求前的完整组装
#                 schema_tokens, total                    （面板②的数据源）
# llm_response    text_preview, tool_calls[{name,args}],  模型响应+用量
#                 usage{prompt,completion,cached}
# schedule        parallel[{name,args}], serial[...]      调度分组+权限判定
# permission      tool, args, decision, answered          权限三态/确认结果
# tool_result     name, args, preview, ok                 单个工具完成
# file_change     op, path, preview                       沙箱面板：文件副作用
# bash_exec       command, exit, preview                  沙箱面板：命令执行
# compact         before, after, dropped                  压缩触发
# subagent_spawn  sa_type, description                    子代理派生
# subagent_done   sa_type, n_tools, tokens, summary       子代理完成
# turn_end        turn, answer_preview                    每轮结束
