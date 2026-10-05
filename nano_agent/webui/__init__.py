"""WebUI：把 nano-agent 的内部机制实时可视化到浏览器的观测台。

启动：python -m nano_agent.webui  （依赖 fastapi + uvicorn，见 requirements.txt）

与终端 REPL 共用同一套 Agent 组装，只多注入两样东西：
1. EventBus（观测总线）——埋点事件全部实时推给浏览器
2. WebConfirm（权限确认桥）——把终端的 y/n/a 交互换成网页按钮

单用户单会话（学习工具，不需要多租户）。
"""
