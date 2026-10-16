from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.events import now_iso
from app.logging_config import get_logger
from app.memory.embedder import Embedder, get_embedder
from app.storage.db_SQlite import execute, fetch_all, fetch_one, insert

logger = get_logger("memory.worldbook")

COLLECTION_NAME = "worldbook"


@dataclass
class WorldbookEntry:
    id: str
    title: str
    keywords: list[str]
    content: str
    importance: float
    status: str
    created_at: str
    updated_at: str


@dataclass
class RetrievedEntry:
    """检索命中的条目"""

    entry: WorldbookEntry
    similarity: float          # 语义相似度
    keyword_hit: bool          # 是否关键词命中
    score: float               # 加权后的综合分


def _row_to_entry(row) -> WorldbookEntry:
    raw_kw = row["keywords"] or ""
    keywords = [k.strip() for k in raw_kw.split(",") if k.strip()]
    return WorldbookEntry(
        id=row["id"],
        title=row["title"],
        keywords=keywords,
        content=row["content"],
        importance=row["importance"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_worldbook_collection(settings: Settings):
    from app.memory.store import get_chroma_client

    client = get_chroma_client(settings)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )


def _entry_to_metadata(entry: WorldbookEntry) -> dict[str, Any]:
    return {
        "title": entry.title,
        "importance": entry.importance,
        "status": entry.status,
    }


async def _chroma_upsert(
    settings: Settings,
    entry: WorldbookEntry,
    embedder: Embedder | None
) -> None:
    try:
        emb = embedder or get_embedder(settings)
        # 向量文本用 title + content，增强召回
        text = f"{entry.title}\n{entry.content}"
        vector = await emb.encode_one(text)
        collection = get_worldbook_collection(settings)
        await asyncio.to_thread(
            lambda: collection.upsert(
                ids=[entry.id],
                embeddings=[vector],
                documents=[text],
                metadatas=[_entry_to_metadata(entry)],
            )
        )
    except Exception:
        logger.exception("worldbook 向量写入失败：id=%s", entry.id)


async def _chroma_delete(
    settings: Settings,
    entry_id: str
) -> None:
    try:
        collection = get_worldbook_collection(settings)
        await asyncio.to_thread(collection.delete, ids=[entry_id])
    except Exception:
        logger.exception("worldbook 向量删除失败：id=%s", entry_id)


async def store_entry(
    settings: Settings,
    *,
    title: str,
    content: str,
    keywords: list[str] | None = None,
    importance: float = 0.7,
    embedder: Embedder | None = None,
) -> WorldbookEntry:
    """新建条目：先 SQLite，再 Chroma"""

    entry_id = uuid4().hex
    now = now_iso()
    kw_str = ",".join(keywords or [])

    await insert(
        settings,
        "INSERT INTO worldbook("
        "id, title, keywords, content, importance, status, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (entry_id, title, kw_str, content, importance, "active", now, now),
    )

    entry = WorldbookEntry(
        id=entry_id,
        title=title,
        keywords=keywords or [],
        content=content,
        importance=importance,
        status="active",
        created_at=now,
        updated_at=now,
    )

    await _chroma_upsert(
        settings,
        entry,
        embedder
    )
    logger.info("worldbook 条目已存储：id=%s title=%r", entry_id, title)
    return entry


async def update_entry(
    settings: Settings,
    entry_id: str,
    *,
    title: str | None = None,
    content: str | None = None,
    keywords: list[str] | None = None,
    importance: float | None = None,
    status: str | None = None,
    embedder: Embedder | None = None,
) -> WorldbookEntry | None:
    """更新条目。title / content / keywords 变更时重建向量。"""
    existing = await get_entry(settings, entry_id)
    if existing is None:
        return None

    now = now_iso()
    new_title = title if title is not None else existing.title
    new_content = content if content is not None else existing.content
    new_keywords = keywords if keywords is not None else existing.keywords
    new_importance = (
        importance if importance is not None else existing.importance
    )
    new_status = status if status is not None else existing.status

    await execute(
        settings,
        "UPDATE worldbook SET title = ?, keywords = ?, content = ?, "
        "importance = ?, status = ?, updated_at = ? WHERE id = ?",
        (
            new_title,
            ",".join(new_keywords),
            new_content,
            new_importance,
            new_status,
            now,
            entry_id,
        ),
    )

    updated = WorldbookEntry(
        id=entry_id,
        title=new_title,
        keywords=new_keywords,
        content=new_content,
        importance=new_importance,
        status=new_status,
        created_at=existing.created_at,
        updated_at=now,
    )

    # 只有 active 的条目参与召回：非 active（deleted / archived）一律从
    # 向量索引移除；从非 active 恢复到 active 时重新写入向量
    if new_status != "active":
        await _chroma_delete(settings, entry_id)
    elif (
        new_title != existing.title
        or new_content != existing.content
        or new_keywords != existing.keywords
        or new_status != existing.status
    ):
        await _chroma_upsert(settings, updated, embedder)

    logger.info("worldbook 条目已更新：id=%s", entry_id)
    return updated


async def delete_entry(
    settings: Settings,
    entry_id: str,
    *,
    hard: bool = False,
) -> bool:
    """删除条目。默认软删 + 从 Chroma 移除。"""
    existing = await get_entry(settings, entry_id)
    if existing is None:
        return False

    if hard:
        await execute(settings, "DELETE FROM worldbook WHERE id = ?", (entry_id,))
    else:
        await execute(
            settings,
            "UPDATE worldbook SET status = 'deleted', updated_at = ? "
            "WHERE id = ?",
            (now_iso(), entry_id),
        )

    await _chroma_delete(settings, entry_id)
    logger.info("worldbook 条目已删除：id=%s hard=%s", entry_id, hard)
    return True


async def get_entry(
    settings: Settings,
    entry_id: str
) -> WorldbookEntry | None:
    row = await fetch_one(
        settings,
        "SELECT * FROM worldbook WHERE id = ?",
        (entry_id,)
    )
    return _row_to_entry(row) if row else None


async def list_entries(
    settings: Settings,
    *,
    status: str | None = "active",
    limit: int = 100,
    offset: int = 0,
) -> list[WorldbookEntry]:
    sql = "SELECT * FROM worldbook WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    rows = await fetch_all(
        settings,
        sql,
        tuple(params)
    )
    return [_row_to_entry(r) for r in rows]


async def get_entries_by_ids(
    settings: Settings,
    entry_ids: list[str]
) -> list[WorldbookEntry]:
    if not entry_ids:
        return []
    placeholders = ",".join("?" * len(entry_ids))
    rows = await fetch_all(
        settings,
        f"SELECT * FROM worldbook WHERE id IN ({placeholders})",
        tuple(entry_ids),
    )
    by_id = {r["id"]: _row_to_entry(r) for r in rows}
    return [by_id[eid] for eid in entry_ids if eid in by_id]


async def count_entries(
    settings: Settings,
    *,
    status: str | None = "active",
) -> int:
    """按与 list_entries 相同的条件统计总数（分页用）"""

    sql = "SELECT COUNT(*) AS n FROM worldbook WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)

    row = await fetch_one(settings, sql, tuple(params))
    return int(row["n"]) if row else 0


def _match_keywords(query: str, keywords: list[str]) -> bool:
    """query 中是否包含任一关键词（大小写不敏感）"""

    if not keywords:
        return False
    q = query.lower()
    return any(kw.lower() in q for kw in keywords if kw)


async def retrieve_entries(
    settings: Settings,
    query: str,
    *,
    top_k: int | None = None,
    embedder: Embedder | None = None,
) -> list[RetrievedEntry]:
    """
    检索 worldbook

    综合分：
        score = similarity
        关键词命中时 + worldbook_keyword_boost
    再按 score 降序，取 top_k
    """

    if not query or not query.strip():
        return []

    top_k = top_k or settings.rag.worldbook_top_k
    if top_k <= 0:
        return []

    emb = embedder or get_embedder(settings)
    query_vector = await emb.encode_one(query)

    collection = get_worldbook_collection(settings)

    try:
        result = await asyncio.to_thread(
            lambda: collection.query(
                query_embeddings=[query_vector],
                n_results=top_k * 3,
                where={"status": "active"},
                include=["distances"],
            )
        )
    except Exception:
        logger.exception("worldbook 检索失败")
        return []

    ids = result["ids"][0] if result["ids"] else []
    distances = result["distances"][0] if result["distances"] else []

    if not ids:
        return []

    entries = await get_entries_by_ids(settings, ids)
    by_id = {e.id: e for e in entries}

    boost = settings.rag.worldbook_keyword_boost
    retrieved: list[RetrievedEntry] = []

    for eid, dist in zip(ids, distances):
        entry = by_id.get(eid)
        if entry is None:
            continue

        similarity = max(0.0, 1.0 - dist)
        kw_hit = _match_keywords(query, entry.keywords)
        score = similarity + (boost if kw_hit else 0.0)

        retrieved.append(
            RetrievedEntry(
                entry=entry,
                similarity=similarity,
                keyword_hit=kw_hit,
                score=score,
            )
        )

    retrieved.sort(key=lambda r: r.score, reverse=True)
    top = retrieved[:top_k]

    if top:
        logger.debug("worldbook 召回 %d 条：query=%r top_score=%.3f kw_hit=%s", len(top), query[:40], top[0].score, top[0].keyword_hit)
    return top


def format_entries_for_prompt(
    entries: list[RetrievedEntry],
    *,
    header: str = "【设定】",
) -> str:
    """格式化"""

    if not entries:
        return ""

    lines = [header]
    for e in entries:
        lines.append(f"- {e.entry.title}：{e.entry.content}")
    return "\n".join(lines)


async def retrieve_entries_and_format(
    settings: Settings,
    query: str,
    *,
    top_k: int | None = None,
    embedder: Embedder | None = None,
    header: str = "【设定】",
) -> str:
    entries = await retrieve_entries(
        settings,
        query,
        top_k=top_k,
        embedder=embedder
    )
    return format_entries_for_prompt(entries, header=header)
