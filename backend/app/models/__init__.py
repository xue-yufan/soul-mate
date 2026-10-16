"""模型适配层"""

from __future__ import annotations

from app.config import Settings
from app.models.base import ChatMessage, ModelAdapter, StreamChunk
from app.models.openai_compatible import OpenAICompatibleAdapter


def build_adapter(settings: Settings) -> ModelAdapter:
    """按配置构造适配器。"""
    name = settings.model.adapter
    if name == "openai_compatible":
        return OpenAICompatibleAdapter(settings.model)
    raise ValueError(f"不支持的模型适配器：{name}")


__all__ = [
    "ChatMessage",
    "ModelAdapter",
    "StreamChunk",
    "build_adapter",
]
