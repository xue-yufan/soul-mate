"""HTTP TTS 适配器的公共骨架

把「通过 HTTP 调一个 TTS 引擎」里所有提供方都一样的部分固定下来：

- 长连接客户端（lifespan 里构建、关服时 aclose）
- 超时放在 httpx.Timeout 上，也就是「拿到锁之后」的净时间，排队等待不算进去
- 串行化：引擎通常只有一个模型实例；而且 busy 是按连接判的（见 app/api/ws.py
  的 sender.turn_task），两个客户端同时说话会并发打进来
- 错误归一化：超时 / 非 2xx / 连不上 → 记 warning 并返回空音频，
  对话照常进行，只是这一句没声音（和 app/tts/edge.py 的约定一致）
- 空音频检测（ws.py 的 TTS worker 会跳过 audio 为空的片段）

子类只需要实现 _request_audio()：发请求、把 (音频字节, 容器格式) 拿出来。
**请求体与响应解析留在子类**——那是各家引擎自己的事，塞进基类就是过早抽象。

格式约定：返回的必须是带容器头的容器格式（mp3 / wav / ogg）。裸 PCM 浏览器放不了，
前端 useTts 的 MIME 映射里也没有可用的裸 PCM 路径。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

import httpx

from app.config import TtsConfig
from app.logging_config import get_logger
from app.tts.base import SynthesizeResult, TtsAdapter

logger = get_logger("tts.http")


class HttpTtsAdapter(TtsAdapter, ABC):
    """HTTP TTS 适配器基类；子类实现 _request_audio()"""

    #: 出不来音频时回给上层的格式（正常路径用子类返回的那个）
    default_format = "wav"

    def __init__(
        self,
        config: TtsConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        # 注入是为了测试（塞 MockTransport）；生产走自己建的那条
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout_s)
        )
        self._owns_client = client is None
        # 引擎只有一个模型实例，一次只喂一个请求
        self._lock = asyncio.Semaphore(1)

    @abstractmethod
    async def _request_audio(
        self, text: str, *, emotion: str | None = None
    ) -> tuple[bytes, str]:
        """发一次请求，返回 (音频字节, 容器格式)

        emotion 是本轮的情绪，按情绪换参考音频的子类才用得上。
        httpx 的异常交给上层的 synthesize() 归一化，子类不用自己兜。
        引擎返回的东西不可解析时（比如 JSON 坏了）直接抛出去：那是代码问题，
        应该让调用方（ws.py 的 TTS worker）在日志里看见，而不是静默变成"没声音"。
        """

        raise NotImplementedError

    async def synthesize(
        self, text: str, *, emotion: str | None = None
    ) -> SynthesizeResult:
        async with self._lock:
            try:
                audio, fmt = await self._request_audio(text, emotion=emotion)
            except httpx.TimeoutException:
                logger.warning(
                    "TTS 请求超时（%ss）：%r", self.config.timeout_s, text[:40]
                )
                return self._silence(text)
            except httpx.HTTPStatusError as exc:
                logger.warning(
                    "TTS 返回非 2xx：status=%s %r",
                    exc.response.status_code,
                    text[:40],
                )
                return self._silence(text)
            except httpx.HTTPError as exc:
                logger.warning(
                    "TTS 请求失败：%s %r", exc.__class__.__name__, text[:40]
                )
                return self._silence(text)

        if not audio:
            logger.warning("TTS 返回空音频：%r", text[:40])
            return self._silence(text)

        return SynthesizeResult(
            audio=audio,
            format=fmt or self.default_format,
            text=text,
            # 项目约定：chars 参与 max_chars_per_turn 截断与 cost_log.tts_chars，
            # 用文本长度，不用引擎自己报的字符数或时长
            chars=len(text),
        )

    def _silence(self, text: str) -> SynthesizeResult:
        """这一句不出声：空 audio 会被 ws.py 的 worker 跳过，不算失败"""

        return SynthesizeResult(
            audio=b"", format=self.default_format, text=text, chars=0
        )

    async def aclose(self) -> None:
        # 只关自己建的；注入的交给调用方（测试）自己收
        if self._owns_client:
            await self.client.aclose()
