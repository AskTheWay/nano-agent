"""task 工具：主 agent 派生子代理的入口。

特殊之处：task 的执行体需要 llm 和权限引擎这两个只在 main 组装时才存在
的依赖，所以用 install() 工厂把它们闭包进去再注册；工具表走全局
REGISTRY（run_task 内部取子集，天然不含 task 自身）。工具函数本身
保持"纯参数 -> 字符串"的形态。
"""

from typing import Annotated

from .registry import tool


def install(llm, permissions, bus=None) -> None:
    """main 组装时调用一次：注册携带依赖闭包的 task 工具。

    子代理类型的参数描述从 SUBAGENT_TYPES 动态生成——往类型表加条目
    后无需改这里，模型侧就能看到新的可选值。
    """
    from ..subagent import SUBAGENT_TYPES
    type_names = ", ".join(SUBAGENT_TYPES)

    @tool(read_only=False)  # 占资源（一次完整的子循环），走串行调度
    def task(description: Annotated[str, "子任务描述，必须自包含：目标、范围、期望产出"],
             agent_type: Annotated[str, f"子代理类型，可用：{type_names}"] = "researcher") -> str:
        """派生一个隔离上下文的子代理去完成调研类子任务，只回传最终摘要。

        适合：需要读很多文件/大范围搜索的调研（避免中间过程污染主上下文）。
        不适合：写文件、执行命令（researcher 是只读的）。
        """
        from ..subagent import run_task
        from . import REGISTRY
        return run_task(llm, REGISTRY, permissions, description, agent_type, bus=bus)
