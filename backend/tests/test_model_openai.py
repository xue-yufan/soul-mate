"""OpenAI 兼容适配器：流式解析、重试边界与超时设置"""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import ModelConfig
from app.models.base import ChatMessage
from app.models.openai_compatible import OpenAICompatibleAdapter

_MESSAGES = [ChatMessage(role="user", content="你好")]


def _config(**overrides) -> ModelConfig:
    base = {
        "base_url": "http://model.test",
        "model": "test-model",
        "timeout_s": 5,
        "connect_timeout_s": 1,
        "max_retries": 2,
        "retry_base_delay_s": 0.0,   # 用例里不退避，别把测试拖慢
    }
    base.update(overrides)
    return ModelConfig(**base)


def _sse(*payloads: dict | str) -> bytes:
    """拼成一条 OpenAI 风格的 SSE 响应体"""

    body = ""
    for p in payloads:
        data = p if isinstance(p, str) else json.dumps(p, ensure_ascii=False)
        body += f"data: {data}\n\n"
    return (body + "data: [DONE]\n\n").encode("utf-8")


def _text_stream(*texts: str) -> bytes:
    payloads: list[dict] = [
        {"choices": [{"delta": {"content": t}}]} for t in texts
    ]
    payloads.append({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    return _sse(*payloads)


def _adapter(handler, **overrides) -> OpenAICompatibleAdapter:
    return OpenAICompatibleAdapter(
        _config(**overrides), transport=httpx.MockTransport(handler)
    )


async def _collect(adapter: OpenAICompatibleAdapter) -> list:
    return [chunk async for chunk in adapter.chat_stream(_MESSAGES)]


async def test_parses_deltas_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"content": "你"}}]},
                {"choices": [{"delta": {"content": "好"}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 11, "completion_tokens": 22},
                },
            ),
        )

    adapter = _adapter(handler)
    try:
        chunks = await _collect(adapter)
    finally:
        await adapter.aclose()

    assert [c.delta for c in chunks if c.delta] == ["你", "好"]
    assert [c.finish_reason for c in chunks if c.finish_reason] == ["stop"]
    assert [(c.tokens_in, c.tokens_out) for c in chunks if c.tokens_in] == [
        (11, 22)
    ]


async def test_accumulates_split_tool_calls():
    """工具调用的 id / 名称 / 参数是分片过来的，要拼成一次完整的调用"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {
                                            "name": "get_time",
                                            "arguments": '{"tz":',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": '"UTC"}'},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            ),
        )

    adapter = _adapter(handler)
    try:
        chunks = await _collect(adapter)
    finally:
        await adapter.aclose()

    calls = [c.tool_calls for c in chunks if c.tool_calls]
    assert len(calls) == 1
    assert calls[0][0].id == "call_1"
    assert calls[0][0].name == "get_time"
    assert calls[0][0].arguments == '{"tz":"UTC"}'


async def test_retries_on_429_then_succeeds():
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, content=b"rate limited")
        return httpx.Response(200, content=_text_stream("你", "好"))

    adapter = _adapter(handler)
    try:
        chunks = await _collect(adapter)
    finally:
        await adapter.aclose()

    assert [c.delta for c in chunks if c.delta] == ["你", "好"]
    assert len(attempts) == 2


async def test_retries_on_network_error():
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("连接被打断", request=request)
        return httpx.Response(200, content=_text_stream("在"))

    adapter = _adapter(handler)
    try:
        chunks = await _collect(adapter)
    finally:
        await adapter.aclose()

    assert [c.delta for c in chunks if c.delta] == ["在"]
    assert len(attempts) == 2


async def test_4xx_is_not_retried():
    """鉴权错 / 参数错重试只是白等，立刻失败"""

    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(401, content=b"unauthorized")

    adapter = _adapter(handler)
    try:
        with pytest.raises(RuntimeError) as excinfo:
            await _collect(adapter)
    finally:
        await adapter.aclose()

    assert "401" in str(excinfo.value)
    assert len(attempts) == 1


async def test_gives_up_after_max_retries():
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503, content=b"unavailable")

    adapter = _adapter(handler, max_retries=2)
    try:
        with pytest.raises(RuntimeError):
            await _collect(adapter)
    finally:
        await adapter.aclose()

    # max_retries=2 → 首次 + 2 次重试
    assert len(attempts) == 3


class _FailingStream(httpx.AsyncByteStream):
    """先吐一段内容，再抛错——模拟生成到一半连接断了"""

    def __init__(self, payload: bytes, exc: Exception) -> None:
        self._payload = payload
        self._exc = exc

    async def __aiter__(self):
        yield self._payload
        raise self._exc


async def test_no_retry_after_content_delivered():
    """契约：已经吐出正文就不能重试，否则同一段话会发两遍"""

    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        # 故意不给 [DONE]：适配器一旦读到 [DONE] 就会 break，
        # 那样流后面的错误就抛不出来，测不到「半路断」这条路径
        half = (
            "data: "
            + json.dumps(
                {"choices": [{"delta": {"content": "半句"}}]},
                ensure_ascii=False,
            )
            + "\n\n"
        ).encode("utf-8")
        return httpx.Response(
            200,
            stream=_FailingStream(
                half,
                httpx.ReadError("流中途断开"),
            ),
        )

    adapter = _adapter(handler)
    got: list = []
    try:
        with pytest.raises(httpx.HTTPError):
            async for chunk in adapter.chat_stream(_MESSAGES):
                got.append(chunk)
    finally:
        await adapter.aclose()

    assert [c.delta for c in got if c.delta] == ["半句"]
    assert len(attempts) == 1


def test_connect_and_read_timeouts_are_split():
    adapter = OpenAICompatibleAdapter(
        _config(timeout_s=42, connect_timeout_s=3)
    )

    assert adapter._client.timeout.read == 42
    assert adapter._client.timeout.connect == 3
