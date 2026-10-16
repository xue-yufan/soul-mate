from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SynthesizeResult:
    """一段合成音频"""

    audio: bytes
    format: str          # mp3 / wav / pcm
    text: str            # 对应的文本
    chars: int           # 字符数，用于成本统计


class TtsAdapter(ABC):
    """TTS 适配器基类"""

    @abstractmethod
    async def synthesize(
        self, text: str, *, emotion: str | None = None
    ) -> SynthesizeResult:
        """合成一段文本，返回音频

        emotion 是本轮的情绪（协议里那 7 个之一，也可能是 None）。
        edge 之类用不上；按情绪换参考音频的本地引擎才需要它。
        """

        raise NotImplementedError

    async def aclose(self) -> None:
        """释放资源"""

        return None
