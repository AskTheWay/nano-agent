# 07. WebUI 观测台：把五层机制实时可视化

> 启动：`python -m nano_agent.webui`，浏览器打开 http://127.0.0.1:8765
> 依赖：`pip install fastapi uvicorn`（可选依赖，只用终端 REPL 可不装）

## 一、这是什么

一个**学习向的观测台**（observability console），不是产品化 UI——
目标是把 agent 每一轮"看不见的内部过程"摊开在面板上：

| 面板 | 看什么 | 数据源事件 |
|---|---|---|
| ① 对话流 | 用户/回答/工具调用卡 + 权限确认按钮 | llm_response / tool_result / permission_ask |
| ② Prompt 组装 | **本轮发给 LLM 的完整构成**：system + 工具 schema + 逐条消息，按 token 占比渲染的堆叠条 | prompt_assembly |
| ③ 上下文 | 预算仪表盘（80% 压缩阈值黄线）、缓存命中、压缩标记 | prompt_assembly / llm_response / compact |
| ④ 调度时间线 | 每轮的并行组/串行组、权限三色徽章、执行结果 | schedule / permission / tool_result |
| ⑤ 沙箱 | 文件副作用（写入/编辑）+ bash 执行，sandbox/ 文件树实时刷新 | file_change / bash_exec / GET /api/sandbox |
| ⑥ 子代理 | sidechain 派生与完成（子上下文规模、只回传摘要） | subagent_spawn / subagent_done |

**最有教学价值的是面板②**：你会亲眼看到第二轮起消息历史如何增长、
工具 schema 始终占据固定的底部、压缩触发后整条堆叠条瞬间缩短。

## 二、实现：观测不侵入逻辑

后端复用与 REPL 完全相同的 Agent 组装，只多注入两样：

1. **EventBus**（`nano_agent/events.py`，~50 行）：线程安全的发布/订阅。
   Agent/ContextManager/子代理的埋点都是 `if self.bus: bus.emit(...)`——
   `bus=None`（终端模式）时零开销。这就是 M3/M4 一路的"注入位"模式的延续。
2. **WebConfirm**：把终端 `input()` 的 y/n/a 换成网页按钮——
   事件发出 + `threading.Event` 阻塞等待 `/api/confirm` 应答（5 分钟超时视为拒绝）。

事件流路径（跨线程 -> 跨协议）：

```
Agent 埋点(任意线程) --> bus --> queue.Queue --> 轮询泵(50ms) --> 所有 WebSocket
```

## 三、几个值得读的实现细节

- **mount("/") 必须放最后**：FastAPI 的 StaticFiles 挂载是前缀兜底，
  定义早于 websocket 路由会把 ws 请求也吞掉（StaticFiles 断言只处理 http scope）——
  这是真实的踩坑，注释在 `server.py` 的 `main()` 里
- **同步 agent 跑在线程池**：`agent.run()` 是阻塞的，`run_in_executor` 包一层，
  事件已经在总线上实时流了——HTTP 响应只负责最终答案
- **事件即快照**：前端不维护 agent 状态的副本，每个事件自带渲染所需的
  全部数据（preview/tokens/args），重连刷新即对齐

## 四、动手玩

```bash
python -m nano_agent.webui
```

1. 问"同时读 README.md 和 requirements.txt 然后总结" → 看④的并行组和②里
   两条 read 结果如何叠进消息历史
2. "在 sandbox 里创建 demo.py 写个打印函数" → 看权限按钮 → ⑤的文件树出现新文件
3. "用 task 派一个子代理调研项目结构" → 看⑥的 sidechain 卡和②的主上下文几乎不涨
4. 把 `TOKEN_LIMIT` 调小重启，多聊几轮 → 看③的黄线被越过、压缩标记、
   ②的堆叠条骤缩
