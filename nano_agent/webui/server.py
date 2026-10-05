"""观测台后端：FastAPI + WebSocket。

事件流：Agent 埋点 --(任意线程)--> bus --> queue --(轮询)--> 所有 WebSocket
权限流：Agent 线程 --permission_ask--> 浏览器弹按钮 --POST /api/confirm--> 放行
"""

import asyncio
import json
import os
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import ui
from ..config import load_config
from ..events import EventBus
from ..agent import Agent, LoopConfig
from ..context import ContextManager
from ..permissions import PermissionEngine
from ..tools import REGISTRY
from ..tools import task as task_tool
from ..main import build_system_prompt

app = FastAPI(title="nano-agent 观测台")

# ========== 全局状态（单会话学习工具） ==========

_bus = EventBus()
_event_q: "queue.Queue[tuple[str, dict]]" = queue.Queue()
_clients: set[WebSocket] = set()
_executor = ThreadPoolExecutor(max_workers=2)  # agent.run 是同步阻塞的
_pending_confirms: dict[str, tuple[threading.Event, dict]] = {}

# 引导期组装（首个请求到达时才真正建 Agent，避免 import 副作用撞上缺 .env）
_agent: Agent | None = None
_agent_lock = threading.Lock()


class WebConfirm:
    """把终端的 y/n/a 交互换成网页按钮：发事件 -> 阻塞等待应答。"""

    def __call__(self, tool_name: str, args: dict) -> str:
        cid = uuid.uuid4().hex[:8]
        done = threading.Event()
        holder: dict = {}
        _bus.emit("permission_ask", id=cid, tool=tool_name,
                  args={k: str(v)[:100] for k, v in list(args.items())[:3]})
        _pending_confirms[cid] = (done, holder)
        done.wait(timeout=300)  # 5 分钟无应答视为拒绝
        _pending_confirms.pop(cid, None)
        return holder.get("answer", "n")


def _build_agent() -> Agent:
    """与 main.py 相同的组装 + bus 注入 + confirm 桥接。"""
    cfg = load_config()
    from ..llm import LLMClient
    llm = LLMClient(cfg)
    perm_path = os.path.join(os.getcwd(), "permissions.json")
    permissions = PermissionEngine(perm_path)
    agent = Agent(llm=llm, tools=REGISTRY, system_prompt=build_system_prompt(),
                  loop_cfg=LoopConfig(max_turns=cfg.max_turns),
                  permissions=permissions, bus=_bus)
    context = ContextManager(agent.messages, llm, cfg.token_limit, bus=_bus)
    context.set_schema_tokens(REGISTRY.schema())
    agent.context = context
    task_tool.install(llm, permissions, bus=_bus)
    context.set_schema_tokens(REGISTRY.schema())  # 多了 task 的定义，重新计入
    ui.confirm = WebConfirm()  # 全局替换：agent 调 ui.confirm 时走网页按钮
    _bus.emit("session_start", model=cfg.model, tools=REGISTRY.names(),
              token_limit=cfg.token_limit,
              rules={"allow": [r.raw for r in permissions.allow],
                     "deny": [r.raw for r in permissions.deny]})
    return agent


def _get_agent() -> Agent:
    global _agent
    with _agent_lock:
        if _agent is None:
            _agent = _build_agent()
        return _agent


# ========== 事件泵：bus(任意线程) -> queue -> WebSocket(async) ==========

_bus.subscribe("*", lambda t, p: _event_q.put((t, p)))


async def _event_pump():
    """轮询线程安全队列，广播到所有浏览器。轮询间隔 50ms 足够实时。"""
    while True:
        try:
            etype, payload = _event_q.get_nowait()
        except queue.Empty:
            await asyncio.sleep(0.05)
            continue
        dead = []
        for ws in _clients:
            try:
                await ws.send_json({"type": etype, **payload})
            except Exception:
                dead.append(ws)
        for ws in dead:
            _clients.discard(ws)


@app.on_event("startup")
async def _start_pump():
    asyncio.create_task(_event_pump())


# ========== HTTP 端点 ==========

@app.get("/")
async def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.post("/api/chat")
async def chat(body: dict):
    """用户消息 -> 线程池里跑 agent.run（阻塞，事件已经在实时推送了）。"""
    user_input = (body.get("input") or "").strip()
    if not user_input:
        return {"ok": False, "error": "empty"}
    loop = asyncio.get_event_loop()
    agent = _get_agent()
    try:
        answer = await loop.run_in_executor(_executor, agent.run, user_input)
        return {"ok": True, "answer": answer}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/confirm")
async def confirm(body: dict):
    """浏览器按钮应答 -> 放行挂起的权限确认。"""
    cid, ans = body.get("id"), body.get("answer", "n")
    pair = _pending_confirms.get(cid)
    if pair:
        done, holder = pair
        holder["answer"] = ans
        done.set()
        return {"ok": True}
    return {"ok": False, "error": "no such confirm"}


@app.post("/api/command")
async def command(body: dict):
    """斜杠命令（/tokens /permissions /clear /compact /history）。"""
    from ..main import _cmd_tokens, _cmd_permissions, _cmd_clear, _cmd_compact, _cmd_history
    cmd = (body.get("cmd") or "").strip()
    agent = _get_agent()
    import io, contextlib
    buf = io.StringIO()
    mapping = {
        "/tokens": _cmd_tokens, "/permissions": _cmd_permissions,
        "/clear": _cmd_clear, "/compact": _cmd_compact, "/history": _cmd_history,
    }
    handler = mapping.get(cmd.split()[0] if cmd else "")
    if not handler:
        return {"ok": False, "error": "未知命令"}
    with contextlib.redirect_stdout(buf):
        handler(agent, *cmd.split()[1:])
    _bus.emit("command_output", cmd=cmd, output=buf.getvalue()[:2000])
    return {"ok": True, "output": buf.getvalue()}


@app.get("/api/sandbox")
async def sandbox():
    """沙箱面板的文件树：sandbox/ 目录实时快照。"""
    root = "sandbox"
    files = []
    if os.path.isdir(root):
        for dirpath, _dirs, names in os.walk(root):
            for n in sorted(names):
                fp = os.path.join(dirpath, n)
                try:
                    size = os.path.getsize(fp)
                    with open(fp, encoding="utf-8", errors="replace") as f:
                        preview = f.read(300)
                except OSError:
                    size, preview = 0, ""
                files.append({"path": fp.replace("\\", "/"), "size": size,
                              "preview": preview})
    return {"files": files}


# ========== WebSocket ==========

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    try:
        while True:
            msg = await ws.receive_json()  # 目前只收心跳；确认走 /api/confirm
            if msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        _clients.discard(ws)


def main() -> None:
    """python -m nano_agent.webui 入口。"""
    import uvicorn
    # 静态资源挂载放最后：mount("/") 是前缀兜底，必须晚于全部 API/ws 路由注册，
    # 否则 websocket 请求会被 StaticFiles 截获（它只处理 http scope，直接断言失败）
    app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")),
              name="static")
    ui.setup_console()
    print("=" * 56)
    print("  nano-agent 观测台 | http://127.0.0.1:8765")
    print("  面板：对话 / Prompt 组装 / 上下文 / 调度 / 沙箱 / 子代理")
    print("=" * 56)
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")


if __name__ == "__main__":
    main()
