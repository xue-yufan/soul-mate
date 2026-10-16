from __future__ import annotations

import asyncio
from typing import Any

from starlette.websockets import WebSocket, WebSocketState


class WsSender:
    """WebSocket 发送包装，带锁"""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()
        # 每连接只提示一次「记忆不可用」，避免每轮对话都刷屏
        self.memory_unavailable_notified = False
        # 当前正在跑的那一轮 agent（后台任务）。
        # 必须放后台：整轮执行期间接收循环要能继续读 tool_confirm / ping / 停机信号
        self.turn_task: asyncio.Task | None = None

    @property
    def client(self) -> Any:
        return self._websocket.client

    @property
    def app_state(self) -> Any:
        """宿主 FastAPI 应用的 state，用于取 settings / lifecycle / runner"""

        return self._websocket.app.state

    @property
    def client_state(self) -> WebSocketState:
        return self._websocket.client_state

    async def send_json(self, payload: dict) -> bool:
        """加锁发送 JSON：返回 True 表示已发送，False 表示连接已关闭或发送失败"""

        async with self._lock:
            if self.client_state != WebSocketState.CONNECTED:
                return False
            try:
                await self._websocket.send_json(payload)
                return True
            except Exception:
                return False

    async def close(self, code: int = 1000) -> None:
        """加锁关闭，已关闭或关闭异常静默忽略"""

        async with self._lock:
            if self.client_state != WebSocketState.CONNECTED:
                return
            try:
                await self._websocket.close(code=code)
            except Exception:
                pass
