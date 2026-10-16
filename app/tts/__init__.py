"""TTS 模块"""

from __future__ import annotations

from app.config import Settings
from app.tts.base import SynthesizeResult, TtsAdapter
from app.tts.edge import EdgeTtsAdapter
from app.tts.gpt_sovits import GptSovitsAdapter
from app.tts.splitter import SentenceSplitter


def build_tts(settings: Settings) -> TtsAdapter:
    """按配置构造 TTS 适配器"""

    provider = settings.tts.provider
    if provider == "edge":
        return EdgeTtsAdapter(settings.tts)
    if provider == "gpt_sovits":
        return GptSovitsAdapter(settings.tts)
    raise ValueError(f"不支持的 TTS 提供方：{provider}")


__all__ = [
    "TtsAdapter",
    "SynthesizeResult",
    "SentenceSplitter",
    "build_tts",
]
