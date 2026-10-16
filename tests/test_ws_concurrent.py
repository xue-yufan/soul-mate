"""并发 send 不交错测试"""

import asyncio

from starlette.websockets import WebSocketState

from app.api.ws_sender import WsSender


class FakeWebSocket:
    """模拟 Starlette WebSocket，只需 send_json / close / client_state。"""

    def __init__(self):
        self.sent: list[dict] = []
        self.client_state = WebSocketState.CONNECTED

    async def send_json(self, payload):
        # 模拟慢发送，制造交错窗口
        await asyncio.sleep(0.01)
        self.sent.append(payload)

    async def close(self, code: int = 1000):
        self.client_state = WebSocketState.DISCONNECTED


async def test_concurrent_sends_serialized():
    ws = FakeWebSocket()
    sender = WsSender(ws)

    async def send_n(n: int, prefix: str):
        for i in range(n):
            await sender.send_json({"from": prefix, "i": i})

    await asyncio.gather(
        send_n(5, "a"),
        send_n(5, "b"),
        send_n(5, "c"),
    )

    # 总共 15 条，没有交错（每条是一个完整 dict）
    assert len(ws.sent) == 15

    # 来自同一 prefix 的消息内部顺序不变
    a_msgs = [m for m in ws.sent if m["from"] == "a"]
    b_msgs = [m for m in ws.sent if m["from"] == "b"]
    c_msgs = [m for m in ws.sent if m["from"] == "c"]

    assert [m["i"] for m in a_msgs] == [0, 1, 2, 3, 4]
    assert [m["i"] for m in b_msgs] == [0, 1, 2, 3, 4]
    assert [m["i"] for m in c_msgs] == [0, 1, 2, 3, 4]


async def test_send_after_close_returns_false():
    """连接关闭后 send_json 返回 False，不抛异常。"""
    ws = FakeWebSocket()
    sender = WsSender(ws)

    ok = await sender.send_json({"hello": "world"})
    assert ok is True
    assert len(ws.sent) == 1

    await sender.close(code=1001)
    assert ws.client_state == WebSocketState.DISCONNECTED

    ok = await sender.send_json({"after": "close"})
    assert ok is False
    assert len(ws.sent) == 1   # 没新增
