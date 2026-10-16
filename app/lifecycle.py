from __future__ import annotations

import asyncio
from typing import Any, Coroutine

from app.events import make_event
from app.logging_config import get_logger

logger = get_logger(__name__)


class Lifecycle:
    """优雅停机管理器"""

    def __init__(
        self,
        version: int,
        shutdown_timeout: float = 10.0,
        ws_notify_delay: float = 0.3,
    ) -> None:
        self.version = version
        self._shutdown_event = asyncio.Event()
        self._ws_connections: set[Any] = set()
        self._background_tasks: set[asyncio.Task] = set()
        self.shutdown_timeout = shutdown_timeout
        self.ws_notify_delay = ws_notify_delay

    @property
    def is_shutting_down(self) -> bool:
        """长任务可轮询此标志，发现关闭则主动退"""

        return self._shutdown_event.is_set()

    def register_ws(self, sender: Any) -> None:
        """注册一个 WebSender 连接"""

        self._ws_connections.add(sender)

    def unregister_ws(self, sender: Any) -> None:
        """注销 WebSender 连接"""

        self._ws_connections.discard(sender)

    def register_task(self, coro: Coroutine) -> asyncio.Task:
        """注册一个后台异步任务：任务完成后自动从集合移除，关闭时会被等待，超时则强制取消"""

        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._on_task_done)
        return task

    def _on_task_done(self, task: asyncio.Task) -> None:
        """任务结束：移出集合并消费异常，避免 'Task exception was never retrieved'"""

        self._background_tasks.discard(task)
        if task.cancelled():
            return

        exc = task.exception()
        if exc is not None:
            logger.error("后台任务异常：%s", task.get_name(), exc_info=exc)

    async def shutdown(self) -> None:
        """按顺序执行优雅停机：通知 WS → 等待任务 → 强制关闭 WS"""

        logger.info("开始优雅停机")
        self._shutdown_event.set()

        await self._notify_ws()
        await self._wait_tasks()
        await self._close_ws()

        logger.info("优雅停机完成")

    async def wait_for_shutdown(self) -> None:
        """等待停机信号。仅供竞速使用，不触发 shutdown"""

        await self._shutdown_event.wait()

    async def _notify_ws(self) -> None:
        """向所有活跃 WS 发送 server_shutdown 事件，给客户端反应时间"""

        if not self._ws_connections:
            return
        logger.info("通知 %d 个 WebSocket 连接关闭", len(self._ws_connections))

        payload = make_event(
            "server_shutdown",
            version=self.version,
            data={"message": "服务即将关闭"}
        )

        for ws in list(self._ws_connections):
            try:
                await ws.send_json(payload)
            except Exception:
                pass

        if self.ws_notify_delay > 0:
            await asyncio.sleep(self.ws_notify_delay)

    async def _wait_tasks(self) -> None:
        """等待后台任务完成，超时则取消"""

        if not self._background_tasks:
            return

        logger.info("等待 %d 个后台任务完成", len(self._background_tasks))
        tasks = list(self._background_tasks)

        done, pending = await asyncio.wait(
            tasks,
            timeout=self.shutdown_timeout,
        )

        if pending:
            logger.warning("后台任务超时，强制取消 %d 个", len(pending))
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def _close_ws(self) -> None:
        """关闭剩余 WS 连接"""

        if not self._ws_connections:
            return

        logger.info("关闭剩余 %d 个 WebSocket 连接", len(self._ws_connections))
        for ws in list(self._ws_connections):
            try:
                await ws.close(code=1001)
            except Exception:
                pass
        self._ws_connections.clear()
