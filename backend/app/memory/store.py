from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from app.config import Settings
from app.events import now_iso
from app.logging_config import get_logger
from app.memory.embedder import Embedder, get_embedder
from app.storage.db_SQlite import execute, fetch_all, fetch_one, insert

logger = get_logger("memory.store")

FactType = Literal["preference", "fact", "event", "emotion"]
FactStatus = Literal["active", "archived", "deleted"]

COLLECTION_NAME = "memory_facts"


@dataclass
class MemoryFact:
    """一条记忆事实"""

    id: str
    type: str
    content: str
    importance: float
    source: str | None
    status: str
    created_at: str
    updated_at: str


def _row_to_fact(row) -> MemoryFact:
    return MemoryFact(
        id=row["id"],
        type=row["type"],
        content=row["content"],
        importance=row["importance"],
        source=row["source"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _fact_to_metadata(fact: MemoryFact) -> dict[str, Any]:
    """Chroma metadata 只支持 str/int/float/bool，None 用空串"""

    return {
        "sqlite_id": fact.id,
        "type": fact.type,
        "importance": float(fact.importance),
        "status": fact.status,
        "source": fact.source or "",
        "created_at": fact.created_at,
    }


_chroma_client = None


def get_chroma_client(settings: Settings):
    """获取全局 Chroma PersistentClient 单例"""

    global _chroma_client
    if _chroma_client is None:
        import chromadb

        chroma_dir = settings.paths.chroma_dir
        chroma_dir.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(chroma_dir))
        logger.info("Chroma 已连接：%s", chroma_dir)
    return _chroma_client


def reset_chroma_client() -> None:
    """重置单例，供测试用"""

    global _chroma_client
    _chroma_client = None


def get_collection(settings: Settings):
    """获取或创建 memory_facts collection"""

    client = get_chroma_client(settings)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


async def _chroma_upsert(
    settings: Settings,
    fact: MemoryFact,
    embedder: Embedder | None
) -> None:
    """写向量：失败只记 warning，可通过 rebuild_index 恢复"""

    try:
        emb = embedder or get_embedder(settings)
        vector = await emb.encode_one(fact.content)
        collection = get_collection(settings)
        await asyncio.to_thread(
            lambda: collection.upsert(
                ids=[fact.id],
                embeddings=[vector],
                documents=[fact.content],
                metadatas=[_fact_to_metadata(fact)],
            )
        )
    except Exception:
        logger.exception("向量写入失败：id=%s，可调 rebuild_index 恢复", fact.id)


async def _chroma_delete(
        settings: Settings,
        fact_id: str
    ) -> None:
    """删向量：失败只记 warning"""

    try:
        collection = get_collection(settings)
        await asyncio.to_thread(collection.delete, ids=[fact_id])
    except Exception:
        logger.exception("向量删除失败：id=%s", fact_id)


async def store_fact(
    settings: Settings,
    *,
    type: str,
    content: str,
    importance: float = 0.5,
    source: str | None = None,
    embedder: Embedder | None = None,
) -> MemoryFact:
    """新建一条记忆：先写 SQLite，再写 Chroma"""

    fact_id = uuid4().hex
    now = now_iso()

    await insert(
        settings,
        "INSERT INTO memory_facts("
        "id, type, content, importance, source, status, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (fact_id, type, content, importance, source, "active", now, now),
    )

    fact = MemoryFact(
        id=fact_id,
        type=type,
        content=content,
        importance=importance,
        source=source,
        status="active",
        created_at=now,
        updated_at=now,
    )

    await _chroma_upsert(settings, fact, embedder)
    logger.info("记忆已存储：id=%s type=%s content=%r", fact_id, type, content[:40])
    return fact


async def update_fact(
    settings: Settings,
    fact_id: str,
    *,
    content: str | None = None,
    importance: float | None = None,
    status: str | None = None,
    embedder: Embedder | None = None,
) -> MemoryFact | None:
    """
    更新记忆

    content 变更时才重新生成向量
    status='deleted' 时从 Chroma 移除
    返回更新后的 fact，不存在返回 None
    """

    existing = await get_fact(settings, fact_id)
    if existing is None:
        return None

    now = now_iso()
    new_content = content if content is not None else existing.content
    new_importance = (
        importance if importance is not None else existing.importance
    )
    new_status = status if status is not None else existing.status

    await execute(
        settings,
        "UPDATE memory_facts SET content = ?, importance = ?, "
        "status = ?, updated_at = ? WHERE id = ?",
        (new_content, new_importance, new_status, now, fact_id),
    )

    updated = MemoryFact(
        id=fact_id,
        type=existing.type,
        content=new_content,
        importance=new_importance,
        source=existing.source,
        status=new_status,
        created_at=existing.created_at,
        updated_at=now,
    )

    # 只有 active 的记忆参与召回：非 active（deleted / archived）一律从
    # 向量索引移除；从非 active 恢复到 active 时重新写入向量
    if new_status != "active":
        await _chroma_delete(settings, fact_id)
    elif new_content != existing.content or new_status != existing.status:
        await _chroma_upsert(settings, updated, embedder)

    logger.info("记忆已更新：id=%s content_changed=%s status=%s", fact_id, content is not None, new_status)
    return updated


async def delete_fact(
    settings: Settings,
    fact_id: str,
    *,
    hard: bool = False,
) -> bool:
    """
    删除记忆

    soft（默认）：status='deleted'，Chroma 移除
    hard：SQLite 物理删除，Chroma 移除
    返回是否命中
    """

    existing = await get_fact(settings, fact_id)
    if existing is None:
        return False

    if hard:
        await execute(
            settings, "DELETE FROM memory_facts WHERE id = ?", (fact_id,)
        )
    else:
        await execute(
            settings,
            "UPDATE memory_facts SET status = 'deleted', updated_at = ? "
            "WHERE id = ?",
            (now_iso(), fact_id),
        )

    await _chroma_delete(settings, fact_id)
    logger.info("记忆已删除：id=%s hard=%s", fact_id, hard)
    return True


async def get_fact(settings: Settings, fact_id: str) -> MemoryFact | None:
    """按 id 取单条，包含所有状态"""

    row = await fetch_one(
        settings,
        "SELECT * FROM memory_facts WHERE id = ?",
        (fact_id,),
    )
    return _row_to_fact(row) if row else None


async def list_facts(
    settings: Settings,
    *,
    status: str | None = "active",
    type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[MemoryFact]:
    """按条件列出，status=None 表示不过滤"""

    sql = "SELECT * FROM memory_facts WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if type is not None:
        sql += " AND type = ?"
        params.append(type)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = await fetch_all(settings, sql, tuple(params))
    return [_row_to_fact(r) for r in rows]


async def get_facts_by_ids(
    settings: Settings, fact_ids: list[str]
) -> list[MemoryFact]:
    """批量按 id 取，保持传入顺序"""

    if not fact_ids:
        return []
    placeholders = ",".join("?" * len(fact_ids))
    rows = await fetch_all(
        settings,
        f"SELECT * FROM memory_facts WHERE id IN ({placeholders})",
        tuple(fact_ids),
    )
    by_id = {r["id"]: _row_to_fact(r) for r in rows}
    return [by_id[fid] for fid in fact_ids if fid in by_id]


async def count_facts(
    settings: Settings,
    *,
    status: str | None = "active",
    type: str | None = None,
) -> int:
    """按与 list_facts 相同的条件统计总数（分页用）"""

    sql = "SELECT COUNT(*) AS n FROM memory_facts WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    if type is not None:
        sql += " AND type = ?"
        params.append(type)

    row = await fetch_one(settings, sql, tuple(params))
    return int(row["n"]) if row else 0


async def search_similar(
    settings: Settings,
    query: str,
    *,
    top_k: int = 20,
    embedder: Embedder | None = None,
) -> list[tuple[str, float]]:
    """
    向量检索，返回 [(fact_id, distance), ...]，distance 越小越相似

    只返回 status='active' 的结果（在 Chroma metadata 里过滤）
    调用方用 get_facts_by_ids 取完整记录
    """

    emb = embedder or get_embedder(settings)
    query_vector = await emb.encode_one(query)
    collection = get_collection(settings)

    result = await asyncio.to_thread(
        lambda: collection.query(
            query_embeddings=[query_vector],
            n_results=top_k,
            where={"status": "active"},
            include=["distances"],
        )
    )

    ids = result["ids"][0] if result["ids"] else []
    distances = result["distances"][0] if result["distances"] else []
    return list(zip(ids, distances))


async def rebuild_index(
    settings: Settings,
    *,
    embedder: Embedder | None = None,
) -> int:
    """
    从 SQLite 重建 Chroma 索引，返回重建条数

    用途：Chroma 写入曾失败，或想彻底重建
    """

    collection = get_collection(settings)

    # 1. 清空 Chroma
    existing = await asyncio.to_thread(collection.get)
    if existing["ids"]:
        await asyncio.to_thread(collection.delete, ids=existing["ids"])
        logger.info("已清空 Chroma：%d 条", len(existing["ids"]))

    # 2. 从 SQLite 读 active
    rows = await fetch_all(
        settings,
        "SELECT * FROM memory_facts WHERE status != 'deleted'",
    )
    if not rows:
        return 0

    facts = [_row_to_fact(r) for r in rows]
    emb = embedder or get_embedder(settings)
    vectors = await emb.encode([f.content for f in facts])

    # 3. 批量 upsert
    await asyncio.to_thread(
        lambda: collection.upsert(
            ids=[f.id for f in facts],
            embeddings=vectors,
            documents=[f.content for f in facts],
            metadatas=[_fact_to_metadata(f) for f in facts],
        )
    )
    logger.info("索引重建完成：%d 条", len(facts))
    return len(facts)
