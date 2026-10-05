"""EventBus 单测：订阅/发布/通配/异常隔离。"""

from nano_agent.events import EventBus


def test_subscribe_and_emit():
    bus = EventBus()
    got = []
    bus.subscribe("turn_start", lambda t, p: got.append((t, p)))
    bus.emit("turn_start", turn=1)
    bus.emit("turn_end", turn=1)  # 未订阅的类型
    assert got == [("turn_start", {"turn": 1})]


def test_wildcard_receives_all():
    bus = EventBus()
    got = []
    bus.subscribe("*", lambda t, p: got.append(t))
    bus.emit("a", x=1)
    bus.emit("b", y=2)
    assert got == ["a", "b"]


def test_callback_exception_swallowed():
    """观测回调炸了不能影响主流程（也没其他订阅者）。"""
    bus = EventBus()
    ok = []
    bus.subscribe("x", lambda t, p: 1 / 0)          # 会炸
    bus.subscribe("x", lambda t, p: ok.append(1))   # 仍应被调用
    bus.emit("x")
    assert ok == [1]


def test_emit_from_thread():
    """线程安全：工具在线程池里执行时也会 emit。"""
    import threading
    bus = EventBus()
    got = []
    bus.subscribe("t", lambda t, p: got.append(p["v"]))
    th = threading.Thread(target=lambda: bus.emit("t", v=42))
    th.start(); th.join()
    assert got == [42]
