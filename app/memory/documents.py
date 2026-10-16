from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from app.config import Settings
from app.events import now_iso
from app.logging_config import get_logger
from app.memory.embedder import Embedder, get_embedder
from app.storage.db_SQlite import execute, fetch_all, fetch_one, insert

logger = get_logger("memory.documents")

COLLECTION_NAME = "documents"


def content_hash(content: str) -> str:
    """内容哈希，用于导入去重"""

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass
class Document:
    id: str
    title: str
    source: str | None
    mime_type: str | None
    size_bytes: int
    chunk_count: int
    status: str
    created_at: str
    updated_at: str


def _row_to_document(row) -> Document:
    return Document(
        id=row["id"],
        title=row["title"],
        source=row["source"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        chunk_count=row["chunk_count"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class DocumentConflictError(RuntimeError):
    """恢复文档时与已有活跃文档的内容哈希冲突（路由层转成 409）"""


class DocumentReindexError(RuntimeError):
    """文档向量重建失败（嵌入模型不可用或 Chroma 出错），状态保持不变"""


def get_documents_collection(settings: Settings):
    from app.memory.store import get_chroma_client

    client = get_chroma_client(settings)
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"}
    )


def split_text(
    text: str,
    *,
    chunk_size: int,
    chunk_overlap: int,
    max_chunks: int,
) -> list[str]:
    """
    按段落优先切分

    段落切分：连续空行分割
    超长段落再按 chunk_size 切，带 overlap
    """

    if not text.strip():
        return []

    # 1. 按空行分段
    paragraphs = [
        p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()
    ]

    chunks: list[str] = []
    buffer = ""

    def flush():
        nonlocal buffer
        if buffer.strip():
            chunks.append(buffer.strip())
        buffer = ""

    for para in paragraphs:
        # 段落超长，先冲掉缓冲，再单独切
        if len(para) >= chunk_size:
            flush()
            chunks.extend(_split_long(para, chunk_size, chunk_overlap))
            continue

        # 加入缓冲
        if len(para) + len(buffer) + 2 <= chunk_size:
            buffer = f"{buffer}\n\n{para}" if buffer else para
        else:
            flush()
            buffer = para

    flush()

    # 上限保护
    if len(chunks) > max_chunks:
        logger.warning("切分块数超过上限 %d，截断（原 %d 块）", max_chunks, len(chunks))
        chunks = chunks[:max_chunks]

    return chunks


def _split_long(
    text: str,
    size: int,
    overlap: int
) -> list[str]:
    """超长文本按字符切，带 overlap"""

    step = max(1, size - overlap)
    return [
        text[i : i + size] for i in range(0, len(text), step) if text[i : i + size].strip()
    ]


async def import_text(
    settings: Settings,
    *,
    title: str,
    content: str,
    source: str | None = None,
    mime_type: str = "text/plain",
    embedder: Embedder | None = None,
) -> Document:
    """
    导入一段文本：切分、嵌入、写入 SQLite + Chroma

    相同内容已存在（status='active'）时直接返回已有文档，不重复写入
    """

    # 1. 去重：相同内容（active）已存在就直接返回
    chash = content_hash(content)
    existing = await fetch_one(
        settings,
        "SELECT * FROM documents WHERE content_hash = ? AND status = 'active'",
        (chash,),
    )
    if existing is not None:
        logger.info("内容已存在，跳过导入：hash=%s title=%r", chash[:12], title)
        return _row_to_document(existing)

    doc_id = uuid4().hex
    now = now_iso()

    # 2. 写 documents：content_hash 留到导入完成时再写，
    #    中途失败留下的半成品行 hash 为 NULL，不会挡住下次重导
    await insert(
        settings,
        "INSERT INTO documents("
        "id, title, source, mime_type, size_bytes, chunk_count, "
        "content_hash, status, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            doc_id, title, source, mime_type,
            len(content.encode("utf-8")),
            0,  # chunk_count 与 content_hash 稍后一起更新
            None,
            "active", now, now,
        ),
    )

    # 3. 切分
    chunks = split_text(
        content,
        chunk_size=settings.rag.chunk_size,
        chunk_overlap=settings.rag.chunk_overlap,
        max_chunks=settings.rag.max_chunks_per_doc,
    )

    if not chunks:
        logger.warning("文档无有效内容：%s", title)
        await _mark_imported(settings, doc_id, 0, chash)
        return await get_document(settings, doc_id)  # type: ignore

    # 4. 写 chunks + 嵌入
    chunk_ids: list[str] = []
    chunk_texts: list[str] = []

    for i, chunk in enumerate(chunks):
        cid = f"{doc_id}:{i}"
        chunk_ids.append(cid)
        chunk_texts.append(chunk)
        await insert(
            settings,
            "INSERT INTO document_chunks("
            "id, document_id, chunk_index, content, created_at"
            ") VALUES (?, ?, ?, ?, ?)",
            (cid, doc_id, i, chunk, now),
        )

    # 4. 嵌入 + 标记导入完成；任何一步失败都要回滚半成品，
    #    否则会留下 status='active' 但 chunk_count=0 的空壳文档
    try:
        emb = embedder or get_embedder(settings)
        vectors = await emb.encode(chunk_texts)

        collection = get_documents_collection(settings)
        await asyncio.to_thread(
            lambda: collection.upsert(
                ids=chunk_ids,
                embeddings=vectors,
                documents=chunk_texts,
                metadatas=[
                    {
                        "document_id": doc_id,
                        "chunk_index": i,
                        "title": title,
                        "source": source or "",
                    }
                    for i in range(len(chunks))
                ],
            )
        )

        # 5. 标记导入完成：chunk_count + content_hash 一起写入
        await _mark_imported(settings, doc_id, len(chunks), chash)
    except aiosqlite.IntegrityError:
        # 竞态：并发导入了同一份内容，另一个请求先完成
        existing = await fetch_one(
            settings,
            "SELECT * FROM documents WHERE content_hash = ? AND status = 'active'",
            (chash,),
        )
        await _discard_partial_import(settings, doc_id, chunk_ids)
        if existing is None:
            raise
        logger.warning("导入竞态，保留已有文档：hash=%s", chash[:12])
        return _row_to_document(existing)
    except Exception:
        logger.exception("文档导入失败，已回滚半成品：id=%s", doc_id)
        await _discard_partial_import(settings, doc_id, chunk_ids)
        raise

    logger.info("文档已导入：id=%s title=%r chunks=%d size=%d", doc_id, title, len(chunks), len(content))

    doc = await get_document(settings, doc_id)
    assert doc is not None
    return doc


async def _mark_imported(
    settings: Settings,
    doc_id: str,
    chunk_count: int,
    chash: str,
) -> None:
    """写入 chunk_count + content_hash，标记这次导入已完成"""

    await execute(
        settings,
        "UPDATE documents SET chunk_count = ?, content_hash = ?, "
        "updated_at = ? WHERE id = ?",
        (chunk_count, chash, now_iso(), doc_id),
    )


async def _discard_partial_import(
    settings: Settings,
    doc_id: str,
    chunk_ids: list[str],
) -> None:
    """丢弃竞态失败的那次导入：删 SQLite 行 + 尽力删向量"""

    await execute(
        settings, "DELETE FROM document_chunks WHERE document_id = ?", (doc_id,)
    )
    await execute(settings, "DELETE FROM documents WHERE id = ?", (doc_id,))

    try:
        collection = get_documents_collection(settings)
        await asyncio.to_thread(collection.delete, ids=chunk_ids)
    except Exception:
        logger.exception("清理竞态导入的向量失败：doc_id=%s", doc_id)


async def import_file(
    settings: Settings,
    path: str | Path,
    *,
    title: str | None = None,
    embedder: Embedder | None = None,
) -> Document:
    """从文件导入，仅支持 UTF-8 文本"""

    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise FileNotFoundError(f"文件不存在：{p}")
    if not p.is_file():
        raise ValueError(f"不是文件：{p}")

    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"不是 UTF-8 文本：{p}")

    return await import_text(
        settings,
        title=title or p.stem,
        content=content,
        source=str(p),
        embedder=embedder,
    )


async def get_document(settings: Settings, doc_id: str) -> Document | None:
    row = await fetch_one(
        settings,
        "SELECT * FROM documents WHERE id = ?",
        (doc_id,)
    )
    return _row_to_document(row) if row else None


async def list_documents(
    settings: Settings,
    *,
    status: str | None = "active",
    limit: int = 100,
    offset: int = 0,
) -> list[Document]:
    sql = "SELECT * FROM documents WHERE 1=1"
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
    return [
        _row_to_document(r) for r in rows
    ]


async def get_chunks(settings: Settings, doc_id: str) -> list[dict]:
    """取某文档所有 chunk"""

    rows = await fetch_all(
        settings,
        "SELECT id, chunk_index, content FROM document_chunks "
        "WHERE document_id = ? ORDER BY chunk_index",
        (doc_id,),
    )
    return [
        {"id": r["id"], "index": r["chunk_index"], "content": r["content"]}
        for r in rows
    ]


async def count_documents(
    settings: Settings,
    *,
    status: str | None = "active",
) -> int:
    """按与 list_documents 相同的条件统计总数（分页用）"""

    sql = "SELECT COUNT(*) AS n FROM documents WHERE 1=1"
    params: list[Any] = []
    if status is not None:
        sql += " AND status = ?"
        params.append(status)

    row = await fetch_one(settings, sql, tuple(params))
    return int(row["n"]) if row else 0


async def delete_document(
    settings: Settings,
    doc_id: str,
    *,
    hard: bool = False,
) -> bool:
    """删除文档，默认软删 + 从 Chroma 移除"""

    doc = await get_document(settings, doc_id)
    if doc is None:
        return False

    if hard:
        await execute(
            settings,
            "DELETE FROM documents WHERE id = ?",
            (doc_id,)
        )
    else:
        await execute(
            settings,
            "UPDATE documents SET status = 'deleted', updated_at = ? WHERE id = ?",
            (now_iso(), doc_id),
        )

    # 从 Chroma 移除所有 chunk
    await _drop_vectors(settings, doc_id, doc.chunk_count)

    logger.info("文档已删除：id=%s hard=%s", doc_id, hard)
    return True


async def _check_hash_conflict(settings: Settings, doc_id: str) -> None:
    """
    恢复前检查 content_hash 冲突

    documents 上有一条部分唯一索引（content_hash WHERE status='active'）。
    同内容的文档被重新导入过之后，再恢复旧的那条，UPDATE 会直接
    IntegrityError；这里提前拦成 DocumentConflictError，让前端能给出
    可操作的提示。
    """

    row = await fetch_one(
        settings, "SELECT content_hash FROM documents WHERE id = ?", (doc_id,)
    )
    chash = row["content_hash"] if row else None
    if chash is None:
        # hash 为空的（导入半途失败留下的残骸）不参与唯一索引，不会冲突
        return

    conflict = await fetch_one(
        settings,
        "SELECT id FROM documents "
        "WHERE content_hash = ? AND status = 'active' AND id != ?",
        (chash, doc_id),
    )
    if conflict is not None:
        raise DocumentConflictError(
            "已存在同内容的活跃文档，恢复会与它冲突；"
            "请先删除那一条，或改用硬删清掉这条历史记录"
        )


async def _drop_vectors(
    settings: Settings,
    doc_id: str,
    chunk_count: int,
) -> None:
    """从 Chroma 移除某文档的所有 chunk 向量（尽力而为，失败只记日志）"""

    chunk_ids = [f"{doc_id}:{i}" for i in range(chunk_count)]
    if not chunk_ids:
        return

    try:
        collection = get_documents_collection(settings)
        await asyncio.to_thread(collection.delete, ids=chunk_ids)
    except Exception:
        logger.exception("从 Chroma 删除文档失败：%s", doc_id)


async def _reindex_document(
    settings: Settings,
    doc: Document,
    *,
    title: str,
    embedder: Embedder | None = None,
) -> None:
    """
    把某文档的 chunk 重新写进 Chroma（恢复软删时用）

    失败一律抛 DocumentReindexError，调用方据此不动 SQLite 里的状态。
    先改状态再写向量的话，嵌入模型不可用或 Chroma 出错会留下
    「列表里是 active、却一条都检索不到」的空壳文档。
    """

    chunks = await get_chunks(settings, doc.id)
    if not chunks:
        # 导入时就没有内容的空文档：没有向量可写，不算失败
        return

    emb = embedder or get_embedder(settings)
    reason = getattr(emb, "unavailable_reason", None)
    if reason is not None:
        raise DocumentReindexError(
            f"嵌入模型不可用（{reason}），暂时无法恢复该文档"
        )

    texts = [c["content"] for c in chunks]
    try:
        vectors = await emb.encode(texts)
        collection = get_documents_collection(settings)
        await asyncio.to_thread(
            lambda: collection.upsert(
                ids=[c["id"] for c in chunks],
                embeddings=vectors,
                documents=texts,
                metadatas=[
                    {
                        "document_id": doc.id,
                        "chunk_index": c["index"],
                        "title": title,
                        "source": doc.source or "",
                    }
                    for c in chunks
                ],
            )
        )
    except Exception as exc:
        logger.exception("重建文档向量失败：id=%s", doc.id)
        raise DocumentReindexError(f"重建文档向量失败：{exc}") from exc


async def _sync_chunk_metadata(
    settings: Settings,
    doc: Document,
    *,
    title: str,
) -> None:
    """
    只改 Chroma 里的 metadata（不重算向量）

    改标题不该依赖嵌入模型，所以走 update 而不是重新 upsert。
    失败只记日志：SQLite 是 source of truth，Chroma 只是索引。
    检索时拼进 prompt 的标题取自 metadata（见 rag.format_documents_for_prompt），
    不跟上的话她还会引用旧名字。
    """

    chunks = await get_chunks(settings, doc.id)
    if not chunks:
        return

    try:
        collection = get_documents_collection(settings)
        await asyncio.to_thread(
            lambda: collection.update(
                ids=[c["id"] for c in chunks],
                metadatas=[
                    {
                        "document_id": doc.id,
                        "chunk_index": c["index"],
                        "title": title,
                        "source": doc.source or "",
                    }
                    for c in chunks
                ],
            )
        )
    except Exception:
        logger.exception("同步文档标题到向量库失败：id=%s", doc.id)


async def update_document(
    settings: Settings,
    doc_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
    embedder: Embedder | None = None,
) -> Document | None:
    """
    更新文档元数据（标题 / 状态）；没传的字段保持原值，文档不存在返回 None

    status='active' 可以用来恢复软删的文档。两条硬约束：

    1. 恢复前先查同 content_hash 的活跃文档（唯一索引会撞），命中就抛
       DocumentConflictError
    2. 先写向量、后改状态：中途失败时文档仍留在 deleted，不会出现空壳
    """

    existing = await get_document(settings, doc_id)
    if existing is None:
        return None

    new_title = title if title is not None else existing.title
    new_status = status if status is not None else existing.status
    title_changed = new_title != existing.title
    status_changed = new_status != existing.status
    leaving_active = status_changed and existing.status == 'active'
    entering_active = status_changed and new_status == 'active'

    # 1. 回到 active：先把向量写回去
    if entering_active:
        await _check_hash_conflict(settings, doc_id)
        await _reindex_document(
            settings, existing, title=new_title, embedder=embedder
        )

    # 2. 离开 active：清掉向量
    if leaving_active:
        await _drop_vectors(settings, doc_id, existing.chunk_count)

    # 3. 落库：标题与状态一次写完
    if title_changed or status_changed:
        await execute(
            settings,
            "UPDATE documents SET title = ?, status = ?, updated_at = ? "
            "WHERE id = ?",
            (new_title, new_status, now_iso(), doc_id),
        )

    # 4. 还活着、只是改了名：把新标题同步进索引的 metadata
    #    （恢复那条不用做，_reindex_document 已经写的是新标题）
    if title_changed and new_status == 'active' and not entering_active:
        await _sync_chunk_metadata(settings, existing, title=new_title)

    logger.info(
        "文档已更新：id=%s title=%r status=%s→%s",
        doc_id,
        new_title,
        existing.status,
        new_status,
    )
    return await get_document(settings, doc_id)
