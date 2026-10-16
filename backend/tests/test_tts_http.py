"""HTTP TTS 适配器基类的行为，不依赖任何真引擎

用 httpx.MockTransport 造一个假引擎，把超时 / 非 2xx / 连不上 / 空响应 / 串行化
这些分支都跑一遍。有一个边界要说清楚：

    MockTransport 绕过传输层，**测不到「httpx.Timeout 真的会打断请求」**。
    所以这里只断言「配置值确实传进了 client」（test_client_timeout_follows_config），
    「超时真的生效」得等接上本地引擎联调时观察。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from app.config import TtsConfig
from app.tts.http_base import HttpTtsAdapter

TEXT = "阁下，早安。"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """不会真的发请求的 client（谁建谁收，正好对上 _owns_client 的语义）"""

    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"\x00")
    )
    fake = httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0))
    yield fake
    await fake.aclose()


class ScriptedAdapter(HttpTtsAdapter):
    """按脚本行事：要么返回构造时给的 (bytes, format)，要么抛构造时给的异常"""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        config: TtsConfig | None = None,
        result: tuple[bytes, str] = (b"\x00\x01", "wav"),
        error: Exception | None = None,
    ) -> None:
        super().__init__(config or TtsConfig(), client=client)
        self.result = result
        self.error = error
        self.calls = 0

    async def _request_audio(
        self, text: str, *, emotion: str | None = None
    ) -> tuple[bytes, str]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


async def test_returns_audio_and_counts_chars(client: httpx.AsyncClient) -> None:
    adapter = ScriptedAdapter(client, result=(b"\x00\x01\x02", "mp3"))

    result = await adapter.synthesize(TEXT)

    assert result.audio == b"\x00\x01\x02"
    assert result.format == "mp3"
    assert result.text == TEXT
    # chars 用文本长度，不用引擎报的数
    assert result.chars == len(TEXT)
    assert adapter.calls == 1


async def test_empty_audio_becomes_silence_without_raising(
    client: httpx.AsyncClient,
) -> None:
    """空音频不是错误：worker 会跳过它，这一句没声音但对话照常"""

    adapter = ScriptedAdapter(client, result=(b"", "wav"))

    result = await adapter.synthesize(TEXT)

    assert result.audio == b""
    assert result.chars == 0


async def test_timeout_is_normalized_to_silence(client: httpx.AsyncClient) -> None:
    adapter = ScriptedAdapter(client, error=httpx.ReadTimeout("too slow"))

    result = await adapter.synthesize(TEXT)

    assert result.audio == b""
    assert result.chars == 0


async def test_http_status_error_is_normalized_to_silence(
    client: httpx.AsyncClient,
) -> None:
    request = httpx.Request("POST", "http://127.0.0.1:9880/tts")
    error = httpx.HTTPStatusError(
        "500", request=request, response=httpx.Response(500, request=request)
    )
    adapter = ScriptedAdapter(client, error=error)

    result = await adapter.synthesize(TEXT)

    assert result.audio == b""


async def test_connect_error_is_normalized_to_silence(
    client: httpx.AsyncClient,
) -> None:
    adapter = ScriptedAdapter(client, error=httpx.ConnectError("refused"))

    result = await adapter.synthesize(TEXT)

    assert result.audio == b""


async def test_default_format_is_used_when_adapter_returns_blank() -> None:
    """子类返回空格式时用 default_format 兜底，别把空串塞进 tts_audio_chunk"""

    class BlankFormat(HttpTtsAdapter):
        async def _request_audio(
            self, text: str, *, emotion: str | None = None
        ) -> tuple[bytes, str]:
            return b"\x00", ""

    adapter = BlankFormat(TtsConfig())
    try:
        result = await adapter.synthesize(TEXT)
        assert result.format == HttpTtsAdapter.default_format
    finally:
        await adapter.aclose()


async def test_client_timeout_follows_config() -> None:
    """配置值真的传进了 client —— 这条是 MockTransport 测不到超时本身时的替代证据"""

    class Noop(HttpTtsAdapter):
        async def _request_audio(
            self, text: str, *, emotion: str | None = None
        ) -> tuple[bytes, str]:
            return b"\x00", "wav"

    adapter = Noop(TtsConfig(timeout_s=7.5))
    try:
        assert adapter.client.timeout == httpx.Timeout(7.5)
    finally:
        await adapter.aclose()


async def test_concurrent_synthesize_is_serialized(
    client: httpx.AsyncClient,
) -> None:
    """
    并发调用必须串行化

    这不是防御性设计：busy 是按连接判的（ws.py 的 sender.turn_task），
    主应用和桌宠两个客户端同时说话就会并发打同一个适配器，而引擎只有一个模型实例。
    """

    active = 0
    peak = 0

    class Slow(HttpTtsAdapter):
        async def _request_audio(
            self, text: str, *, emotion: str | None = None
        ) -> tuple[bytes, str]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return b"\x00", "wav"

    adapter = Slow(TtsConfig(), client=client)

    results = await asyncio.gather(
        *(adapter.synthesize(f"第 {i} 句") for i in range(5))
    )

    assert peak == 1
    assert [r.chars for r in results] == [len(f"第 {i} 句") for i in range(5)]


async def test_injected_client_is_not_closed(client: httpx.AsyncClient) -> None:
    """注入的 client 归调用方管，aclose() 不该把它关掉"""

    adapter = ScriptedAdapter(client)

    await adapter.aclose()

    assert client.is_closed is False


async def test_emotion_is_passed_to_request_audio(client: httpx.AsyncClient) -> None:
    """按情绪换参考音频，靠这个参数从合成入口传到子类"""

    seen: list[str | None] = []

    class Recorder(HttpTtsAdapter):
        async def _request_audio(
            self, text: str, *, emotion: str | None = None
        ) -> tuple[bytes, str]:
            seen.append(emotion)
            return b"\x00", "wav"

    adapter = Recorder(TtsConfig())
    try:
        await adapter.synthesize(TEXT, emotion="happy")
        await adapter.synthesize(TEXT)
    finally:
        await adapter.aclose()

    assert seen == ["happy", None]
