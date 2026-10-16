"""GptSovitsAdapter 的单元测试

不依赖真引擎：用 httpx.MockTransport 造假服务端。
真引擎的接入观察（冷启动耗时、输出时长）留在 RESULTS / improvement-plan，不进这里。
"""

from __future__ import annotations

import httpx
import pytest

from app.config import TtsConfig
from app.tts.gpt_sovits import GptSovitsAdapter


def _config(**overrides) -> TtsConfig:
    base = dict(
        provider="gpt_sovits",
        base_url="http://127.0.0.1:9880",
        ref_audio="D:/GPT-SoVITS/ref.wav",
        prompt_text="冷么？要不要一起去日光下走走？",
        timeout_s=60.0,
    )
    base.update(overrides)
    return TtsConfig(**base)


def _adapter(handler, **config_overrides) -> GptSovitsAdapter:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(60.0),
    )
    return GptSovitsAdapter(_config(**config_overrides), client=client)


# 一段最小合法 wav：RIFF 头 + 0 数据
FAKE_WAV = (
    b"RIFF" + (36).to_bytes(4, "little") + b"WAVE"
    + b"fmt " + (16).to_bytes(4, "little")
    + (1).to_bytes(2, "little") + (1).to_bytes(2, "little")
    + (16000).to_bytes(4, "little") + (32000).to_bytes(4, "little")
    + (2).to_bytes(2, "little") + (16).to_bytes(2, "little")
    + b"data" + (0).to_bytes(4, "little")
)


@pytest.mark.asyncio
async def test_success_returns_wav_and_len_chars():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/tts"
        return httpx.Response(
            200, content=FAKE_WAV, headers={"content-type": "audio/wav"}
        )

    adapter = _adapter(handler)
    result = await adapter.synthesize("你好")
    assert result.audio == FAKE_WAV
    assert result.format == "wav"
    assert result.text == "你好"
    assert result.chars == 2
    await adapter.aclose()


@pytest.mark.asyncio
async def test_request_body_has_required_fields():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        captured.update(json.loads(request.content))
        return httpx.Response(200, content=FAKE_WAV)

    adapter = _adapter(handler)
    await adapter.synthesize("测试文本")
    assert captured == {
        "text": "测试文本",
        "text_lang": "zh",
        "ref_audio_path": "D:/GPT-SoVITS/ref.wav",
        "prompt_text": "冷么？要不要一起去日光下走走？",
        "prompt_lang": "zh",
        "media_type": "wav",
        "streaming_mode": False,
        # ① 声音起伏：默认值 = 引擎默认（音色最稳）
        "temperature": 1.0,
        "top_k": 15,
        "top_p": 1.0,
    }
    await adapter.aclose()


@pytest.mark.asyncio
async def test_non_2xx_returns_silence():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"message": "tts failed", "Exception": "Files not found: xxx"},
        )

    adapter = _adapter(handler)
    result = await adapter.synthesize("你好")
    assert result.audio == b""
    assert result.chars == 0
    await adapter.aclose()


@pytest.mark.asyncio
async def test_timeout_is_normalized_to_silence():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated", request=request)

    adapter = _adapter(handler)
    result = await adapter.synthesize("你好")
    assert result.audio == b""
    await adapter.aclose()


@pytest.mark.asyncio
async def test_connect_error_is_normalized_to_silence():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    adapter = _adapter(handler)
    result = await adapter.synthesize("你好")
    assert result.audio == b""
    await adapter.aclose()


@pytest.mark.asyncio
async def test_empty_body_is_silence():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    adapter = _adapter(handler)
    result = await adapter.synthesize("你好")
    assert result.audio == b""
    await adapter.aclose()


@pytest.mark.asyncio
async def test_missing_base_url_is_silence():
    adapter = _adapter(lambda r: httpx.Response(500), base_url="")
    result = await adapter.synthesize("你好")
    assert result.audio == b""
    await adapter.aclose()


@pytest.mark.asyncio
async def test_missing_ref_audio_is_silence():
    adapter = _adapter(lambda r: httpx.Response(500), ref_audio="")
    result = await adapter.synthesize("你好")
    assert result.audio == b""
    await adapter.aclose()


@pytest.mark.asyncio
async def test_missing_prompt_text_is_silence():
    adapter = _adapter(lambda r: httpx.Response(500), prompt_text="")
    result = await adapter.synthesize("你好")
    assert result.audio == b""
    await adapter.aclose()


@pytest.mark.asyncio
async def test_serialized_by_lock():
    """并发调用时，同一时刻只应有 1 个请求在飞行中"""
    import asyncio

    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return httpx.Response(200, content=FAKE_WAV)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(60.0))
    adapter = GptSovitsAdapter(_config(), client=client)

    await asyncio.gather(*(adapter.synthesize(f"句子{i}") for i in range(5)))
    assert peak == 1
    await adapter.aclose()
