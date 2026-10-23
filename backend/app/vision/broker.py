from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.events import make_event
from app.logging_config import get_logger

logger = get_logger("vision.broker")

CAMERA_CAPABILITY = "camera"


class CameraCaptureError(RuntimeError):
    """摄像头抓帧失败。"""


class CameraUnavailableError(CameraCaptureError):
    """当前没有已授权的摄像头客户端。"""


class CameraTimeoutError(CameraCaptureError):
    """等待前端返回画面超时。"""


@dataclass(frozen=True)
class CameraFrame:
    image: bytes
    mime_type: str


@dataclass
class _PendingCapture:
    sender: Any
    future: asyncio.Future[CameraFrame]
    request_id: str


class CameraBroker:
    """按需向前端摄像头窗口请求一帧。

    WebSocket 连接通过 client_hello 声明 camera 能力。后端只会在能力声明、
    用户开启摄像头之后调用这里；断开连接时会立即让等待中的工具失败，避免
    Agent 一直挂到整轮超时。
    """

    def __init__(self) -> None:
        self._clients: dict[Any, set[str]] = {}
        self._pending: dict[str, _PendingCapture] = {}

    def register(self, sender: Any, capabilities: list[str]) -> None:
        caps = {cap for cap in capabilities if cap == CAMERA_CAPABILITY}
        if caps:
            self._clients[sender] = caps
            logger.info("摄像头客户端已登记：client=%s", sender.client)
        else:
            self._clients.pop(sender, None)

    def unregister(self, sender: Any) -> None:
        self._clients.pop(sender, None)
        for capture_id, pending in list(self._pending.items()):
            if pending.sender is not sender:
                continue
            self._pending.pop(capture_id, None)
            if not pending.future.done():
                pending.future.set_exception(CameraUnavailableError("摄像头连接已断开"))

    def has_camera_client(self) -> bool:
        return any(CAMERA_CAPABILITY in caps for caps in self._clients.values())

    def _pick_camera_client(self) -> Any:
        for sender, caps in list(self._clients.items()):
            if CAMERA_CAPABILITY in caps:
                return sender
        raise CameraUnavailableError("用户尚未开启摄像头，或摄像头窗口未连接")

    async def request_capture(
        self,
        *,
        version: int,
        request_id: str,
        session_id: str | None,
        question: str,
        reason: str,
        timeout_s: float,
    ) -> CameraFrame:
        sender = self._pick_camera_client()
        capture_id = uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[CameraFrame] = loop.create_future()
        self._pending[capture_id] = _PendingCapture(
            sender=sender,
            future=future,
            request_id=request_id,
        )

        payload = make_event(
            "camera_capture_request",
            version=version,
            request_id=request_id,
            session_id=session_id,
            data={
                "capture_id": capture_id,
                "question": question,
                "reason": reason,
                "timeout_s": timeout_s,
            },
        )
        sent = await sender.send_json(payload)
        if not sent:
            self._pending.pop(capture_id, None)
            if not future.done():
                future.cancel()
            raise CameraUnavailableError("摄像头窗口没有响应")

        logger.info(
            "等待摄像头抓帧：capture_id=%s request_id=%s timeout=%ss",
            capture_id,
            request_id,
            timeout_s,
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except TimeoutError as exc:
            raise CameraTimeoutError("等待摄像头画面超时") from exc
        finally:
            self._pending.pop(capture_id, None)

    def resolve(
        self,
        sender: Any,
        capture_id: str,
        *,
        ok: bool,
        image: bytes | None = None,
        mime_type: str = "",
        error: str = "",
    ) -> bool:
        pending = self._pending.get(capture_id)
        if pending is None or pending.sender is not sender or pending.future.done():
            logger.warning("忽略无效或过期的摄像头结果：capture_id=%s", capture_id)
            return False

        self._pending.pop(capture_id, None)
        if not ok:
            pending.future.set_exception(CameraCaptureError(error or "摄像头没有返回画面"))
            return True

        if not image:
            pending.future.set_exception(CameraCaptureError("摄像头返回了空画面"))
            return True

        pending.future.set_result(CameraFrame(image=image, mime_type=mime_type or "image/jpeg"))
        return True
