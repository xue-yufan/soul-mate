from __future__ import annotations

import io

from app.config import TtsConfig
from app.logging_config import get_logger
from app.tts.base import SynthesizeResult, TtsAdapter

logger = get_logger("tts.edge")


class EdgeTtsAdapter(TtsAdapter):
    """Edge TTS 适配器"""

    def __init__(self, config: TtsConfig) -> None:
        self.config = config

    async def synthesize(
        self, text: str, *, emotion: str | None = None
    ) -> SynthesizeResult:
        # emotion 收下但不用：edge 没有"按情绪换参考音频"这一说
        import edge_tts

        communicate = edge_tts.Communicate(
            text=text,
            voice=self.config.voice,
            rate=self.config.rate,
            volume=self.config.volume,
        )

        buf = io.BytesIO()
        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buf.write(chunk["data"])
        except edge_tts.exceptions.NoAudioReceived:
            # 纯标点等拿不到音频时 Edge 会抛该异常，按空音频处理，不让异常冒泡
            logger.warning("Edge TTS 无音频返回：text=%r", text[:40])
            return SynthesizeResult(audio=b"", format="mp3", text=text, chars=0)

        audio = buf.getvalue()
        if not audio:
            logger.warning("Edge TTS 返回空音频：text=%r", text[:40])
            return SynthesizeResult(audio=b"", format="mp3", text=text, chars=0)

        return SynthesizeResult(audio=audio, format="mp3", text=text, chars=len(text))

    async def aclose(self) -> None:
        # Edge TTS 无持久连接，无需清理
        return None
