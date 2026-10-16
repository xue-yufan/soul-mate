from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.logging_config import get_logger

logger = get_logger("permission.pending")


@dataclass
class PendingConfirm:
    """一个待确认的工具调用"""

    request_id: str
    tool_name: str
    args: dict[str, Any]
    event: asyncio.Event = field(default_factory=asyncio.Event)
    allowed: bool | None = None


class PendingRegistry:
    """挂起表"""

    def __init__(self) -> None:
        self._pending: dict[str, PendingConfirm] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        request_id: str,
        tool_name: str,
        args: dict[str, Any]
    ) -> PendingConfirm:
        """创建挂起槽位"""

        async with self._lock:
            pending = PendingConfirm(
                request_id=request_id,
                tool_name=tool_name,
                args=args
            )
            self._pending[request_id] = pending
            return pending

    async def resolve(
            self,
            request_id: str,
            allowed: bool
        ) -> bool:
        """
        前端回复后调用

        返回 True 表示命中并唤醒，False 表示没有对应的挂起
        """

        async with self._lock:
            pending = self._pending.get(request_id)
            if pending is None:
                logger.warning("确认回复无对应挂起：request_id=%s", request_id)
                return False
            pending.allowed = allowed
            pending.event.set()
            return True

    async def wait(
        self,
        pending: PendingConfirm,
        timeout_s: float,
    ) -> bool | None:
        """
        等待前端回复

        返回：
        - True  前端同意
        - False 前端拒绝
        - None  超时或取消
        """

        try:
            await asyncio.wait_for(
                pending.event.wait(),
                timeout=timeout_s
            )
        except asyncio.TimeoutError:
            logger.info("工具确认超时：request_id=%s tool=%s", pending.request_id, pending.tool_name)
            return None
        finally:
            async with self._lock:
                self._pending.pop(pending.request_id, None)

        return pending.allowed

    async def discard(self, request_id: str) -> None:
        """丢弃挂起，不再等待"""

        async with self._lock:
            self._pending.pop(request_id, None)

    def count(self) -> int:
        return len(self._pending)


_registry: PendingRegistry | None = None


def get_pending_registry() -> PendingRegistry:
    global _registry
    if _registry is None:
        _registry = PendingRegistry()
    return _registry


def reset_pending_registry() -> None:
    global _registry
    _registry = None
