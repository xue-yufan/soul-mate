"""摄像头抓帧协调与本机视觉模型解析。"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.vision import (
    CameraBroker,
    CameraUnavailableError,
    VisionObserver,
)


class FakeSender:
    def __init__(self) -> None:
        self.client = "test-client"
        self.sent: list[dict] = []

    async def send_json(self, payload: dict) -> bool:
        self.sent.append(payload)
        return True


async def test_camera_broker_round_trip() -> None:
    broker = CameraBroker()
    sender = FakeSender()
    broker.register(sender, ["camera"])

    task = asyncio.create_task(
        broker.request_capture(
            version=1,
            request_id="turn-1",
            session_id="default",
            question="用户在做什么？",
            reason="需要眼前画面",
            timeout_s=1.0,
        )
    )
    await asyncio.sleep(0)

    request = sender.sent[0]
    assert request["type"] == "camera_capture_request"
    assert request["request_id"] == "turn-1"
    capture_id = request["data"]["capture_id"]

    assert broker.resolve(
        sender,
        capture_id,
        ok=True,
        image=b"fake-jpeg",
        mime_type="image/jpeg",
    )
    frame = await task
    assert frame.image == b"fake-jpeg"
    assert frame.mime_type == "image/jpeg"


async def test_camera_broker_requires_registered_client() -> None:
    with pytest.raises(CameraUnavailableError):
        await CameraBroker().request_capture(
            version=1,
            request_id="turn-1",
            session_id="default",
            question="看看",
            reason="",
            timeout_s=0.1,
        )


async def test_camera_broker_disconnect_fails_pending() -> None:
    broker = CameraBroker()
    sender = FakeSender()
    broker.register(sender, ["camera"])

    task = asyncio.create_task(
        broker.request_capture(
            version=1,
            request_id="turn-1",
            session_id="default",
            question="看看",
            reason="",
            timeout_s=1.0,
        )
    )
    await asyncio.sleep(0)
    broker.unregister(sender)

    with pytest.raises(CameraUnavailableError):
        await task


async def test_vision_observer_sends_frame_and_parses_structured_json(
    tmp_settings,
) -> None:
    seen: dict = {}
    observation_json = {
        "summary": "用户坐在桌前",
        "details": ["画面中有键盘"],
        "people_count": 1,
        "objects": ["键盘", "杯子"],
        "activity": "使用电脑",
        "uncertainties": [],
        "confidence": 0.88,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(observation_json, ensure_ascii=False)}},
        )

    observer = VisionObserver(
        tmp_settings,
        transport=httpx.MockTransport(handler),
    )
    observation = await observer.observe(
        b"fake-jpeg",
        question="用户在做什么？",
    )

    assert seen["path"] == "/api/chat"
    message = seen["payload"]["messages"][0]
    assert message["images"]
    assert seen["payload"]["model"] == tmp_settings.vision.model
    assert observation.summary == "用户坐在桌前"
    assert observation.objects == ["键盘", "杯子"]
    assert observation.confidence == 0.88
