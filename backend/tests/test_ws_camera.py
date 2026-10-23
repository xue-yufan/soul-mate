"""WS 摄像头协商、抓帧请求与结果回传。"""

from __future__ import annotations

import base64

from app.agent.runner import AgentEvent


class CameraRunner:
    def __init__(self) -> None:
        self.adapter = None
        self.frame = None

    async def run(
        self,
        user_input,
        *,
        should_stop=None,
        request_id=None,
        session_id=None,
        exclude_message_id=None,
        on_tool_event=None,
        pending_registry=None,
        camera_broker=None,
    ):
        self.frame = await camera_broker.request_capture(
            version=1,
            request_id=request_id,
            session_id=session_id,
            question="用户在做什么？",
            reason="用户要求看看",
            timeout_s=1.0,
        )
        yield AgentEvent("run_started", {})
        yield AgentEvent("text_delta", {"text": "看到了"})
        yield AgentEvent(
            "run_finished",
            {
                "text": "看到了",
                "tokens_in": 1,
                "tokens_out": 1,
                "model": "fake-model",
            },
        )


def test_ws_camera_capture_round_trip(client, tmp_settings):
    runner = CameraRunner()
    client.app.state.agent_runner = runner

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "client_hello",
                "request_id": "hello-1",
                "data": {"capabilities": ["camera"]},
            }
        )
        ws.send_json(
            {
                "v": 1,
                "type": "text_input",
                "request_id": "turn-1",
                "data": {"text": "看看我"},
            }
        )

        request = ws.receive_json()
        assert request["type"] == "camera_capture_request"
        assert request["request_id"] == "turn-1"
        capture_id = request["data"]["capture_id"]

        ws.send_json(
            {
                "v": 1,
                "type": "camera_capture_result",
                "request_id": capture_id,
                "data": {
                    "capture_id": capture_id,
                    "ok": True,
                    "mime_type": "image/jpeg",
                    "image": base64.b64encode(b"fake-jpeg").decode("ascii"),
                },
            }
        )

        frames = []
        while True:
            event = ws.receive_json()
            frames.append(event)
            if event["type"] == "run_finished":
                break

    assert [event["type"] for event in frames] == [
        "text_delta",
        "run_finished",
    ]
    assert runner.frame is not None
    assert runner.frame.image == b"fake-jpeg"
