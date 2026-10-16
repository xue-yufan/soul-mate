from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Sequence

from app.config import Settings
from app.logging_config import get_logger

logger = get_logger("memory.embedder")


class Embedder:
    """本地嵌入模型包装"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # SentenceTransformer，懒加载
        self._model = None
        self._load_lock = asyncio.Lock()
        self._dim: int | None = None
        # 加载失败后置位：记录原因，后续调用直接快速失败，不再每次重试十几秒
        self._unavailable_reason: str | None = None

    @property
    def unavailable_reason(self) -> str | None:
        """嵌入模型不可用的原因；None 表示可用（或尚未尝试加载）"""

        return self._unavailable_reason

    def mark_unavailable(self, reason: str) -> None:
        """标记为不可用：调用方据此跳过召回、降级对话"""

        self._unavailable_reason = reason
        logger.error("嵌入模型标记为不可用：%s", reason)


    def _resolve_model_path(self) -> Path:
        """
        从配置解析模型目录

        embed_model_dir 是 Optional，未配置时抛异常
        调用方在启动阶段可主动调 ensure_configured() 提前暴露问题
        """

        model_dir = self.settings.paths.embed_model_dir
        if model_dir is None:
            raise RuntimeError("paths.embed_model_dir 未配置")
        model_path = Path(model_dir)
        if not model_path.exists():
            raise RuntimeError(f"嵌入模型路径不存在：{model_path}")
        return model_path

    @staticmethod
    def ensure_configured(settings: Settings) -> None:
        """
        启动时校验配置

        features.memory 打开时应在 lifespan 里调一次，
        让配置错误在启动阶段暴露，而不是第一次对话时
        """

        if settings.paths.embed_model_dir is None:
            raise RuntimeError("features.memory 已开启，但 paths.embed_model_dir 未配置")

        if not Path(settings.paths.embed_model_dir).exists():
            raise RuntimeError(f"嵌入模型路径不存在：{settings.paths.embed_model_dir}")


    def _load_sync(self):
        """同步加载模型，在线程池里执行"""

        from sentence_transformers import SentenceTransformer

        model_path = self._resolve_model_path()
        device = self.settings.embedding.device

        logger.info("加载嵌入模型：path=%s, device=%s", model_path, device)
        model = SentenceTransformer(str(model_path), device=device)
        self._dim = model.get_embedding_dimension()
        logger.info("嵌入模型已加载：dim=%d", self._dim)
        return model


    async def _ensure_loaded(self) -> None:
        """确保模型已加载 并发安全"""

        if self._model is not None:
            return
        if self._unavailable_reason is not None:
            raise RuntimeError(f"嵌入模型不可用：{self._unavailable_reason}")
        async with self._load_lock:
            if self._model is not None:
                return
            if self._unavailable_reason is not None:
                raise RuntimeError(f"嵌入模型不可用：{self._unavailable_reason}")
            try:
                self._model = await asyncio.to_thread(self._load_sync)
            except Exception as e:
                # 加载失败基本是配置/环境问题，重试每次都卡十几秒，
                # 这里直接降级：本轮及后续都不再使用记忆，直到进程重启
                self.mark_unavailable(str(e) or e.__class__.__name__)
                raise


    def _encode_sync(self, text: Sequence[str]) -> list[list[float]]:
        """同步编码，在线程池里执行"""

        normalize = self.settings.embedding.normalize
        batch_size = self.settings.embedding.batch_size
        vectors = self._model.encode(
            list(text),
            batch_size=batch_size,
            normalize_embeddings=normalize,
            show_progress_bar=False,
        )
        return vectors.tolist()


    async def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """异步编码一批文本，返回向量列表"""

        if not texts:
            return []
        await self._ensure_loaded()
        return await asyncio.to_thread(self._encode_sync, texts)


    async def encode_one(self, text: str) -> list[float]:
        """异步编码单条文本"""

        vectors = await self.encode([text])
        return vectors[0]


    @property
    def dimension(self) -> int | None:
        return self._dim

    @property
    def loaded(self) -> bool:
        return self._model is not None

    async def warmup(self) -> None:
        """预热：主动加载模型并编码一次"""

        await self._ensure_loaded()
        await self.encode(["warmup"])


_embedder: Embedder | None = None


def get_embedder(settings: Settings) -> Embedder:
    """获取全局 Embedder 单例，首次调用时创建，不加载模型"""

    global _embedder
    if _embedder is None:
        _embedder = Embedder(settings)
    return _embedder


def reset_embedder() -> None:
    """重置单例，供测试用"""

    global _embedder
    _embedder = None
