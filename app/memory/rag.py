from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import Settings
from app.logging_config import get_logger
from app.memory.documents import get_documents_collection
from app.memory.embedder import Embedder, get_embedder

logger = get_logger("memory.rag")


@dataclass
class RetrievedChunk:
    """一个命中的文档片段。"""

    chunk_id: str
    document_id: str
    chunk_index: int
    title: str
    source: str
    content: str
    similarity: float       # 0~1，越大越相似


async def retrieve_documents(
    settings: Settings,
    query: str,
    *,
    top_k: int | None = None,
    min_similarity: float | None = None,
    embedder: Embedder | None = None,
) -> list[RetrievedChunk]:
    """
    向量检索文档片段

    top_k / min_similarity 默认从 settings.rag 读
    返回按 similarity 降序的结果
    """

    if not query or not query.strip():
        return []

    top_k = top_k or settings.rag.top_k
    if top_k <= 0:
        return []

    min_sim = min_similarity if min_similarity is not None else settings.rag.min_similarity
    emb = embedder or get_embedder(settings)
    query_vector = await emb.encode_one(query)
    collection = get_documents_collection(settings)

    # 多取一些候选：按内容去重后还要保证有 top_k 条
    candidates = top_k * 3

    try:
        result = await asyncio.to_thread(
            lambda: collection.query(
                query_embeddings=[query_vector],
                n_results=candidates,
                include=["documents", "metadatas", "distances"],
            )
        )
    except Exception:
        logger.exception("文档检索失败")
        return []

    ids = result["ids"][0] if result["ids"] else []
    docs = result["documents"][0] if result["documents"] else []
    metas = result["metadatas"][0] if result["metadatas"] else []
    distances = result["distances"][0] if result["distances"] else []

    chunks: list[RetrievedChunk] = []
    for cid, doc, meta, dist in zip(ids, docs, metas, distances):
        similarity = max(0.0, 1.0 - dist)
        if similarity < min_sim:
            continue
        chunks.append(
            RetrievedChunk(
                chunk_id=cid,
                document_id=meta.get("document_id", ""),
                chunk_index=int(meta.get("chunk_index", 0)),
                title=meta.get("title", ""),
                source=meta.get("source", ""),
                content=doc,
                similarity=similarity,
            )
        )

    # 不依赖 Chroma 的返回顺序，显式排序后再去重
    chunks.sort(key=lambda c: c.similarity, reverse=True)

    # 按内容去重：相同片段只保留相似度最高的那条
    seen: set[str] = set()
    deduped: list[RetrievedChunk] = []
    for c in chunks:
        key = c.content.strip()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    chunks = deduped[:top_k]

    if chunks:
        logger.debug("文档召回 %d 条：query=%r top_sim=%.3f", len(chunks), query[:40], chunks[0].similarity)
    return chunks


def format_documents_for_prompt(
    chunks: list[RetrievedChunk],
    *,
    header: str = "【相关文档】",
) -> str:
    """
    格式化成可注入 system prompt 的文本

    形如：
        【相关文档】
        - [标题] 片段内容
        - [标题] 片段内容
    """

    if not chunks:
        return ""

    lines = [header]
    for c in chunks:
        prefix = f"[{c.title}] " if c.title else ""
        lines.append(f"- {prefix}{c.content}")
    return "\n".join(lines)


async def retrieve_documents_and_format(
    settings: Settings,
    query: str,
    *,
    top_k: int | None = None,
    min_similarity: float | None = None,
    embedder: Embedder | None = None,
    header: str = "【相关文档】",
) -> str:
    """检索并格式化，空结果返回空串"""

    chunks = await retrieve_documents(
        settings,
        query,
        top_k=top_k,
        min_similarity=min_similarity,
        embedder=embedder,
    )
    return format_documents_for_prompt(chunks, header=header)
