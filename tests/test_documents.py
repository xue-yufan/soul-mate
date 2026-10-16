"""文档导入去重与检索去重测试（不依赖真模型 / 真 Chroma）"""

from __future__ import annotations

import aiosqlite
import pytest

from app.memory.documents import (
    DocumentConflictError,
    DocumentReindexError,
    count_documents,
    delete_document,
    import_text,
    update_document,
)
from app.memory.rag import retrieve_documents
from app.storage.db_SQlite import execute, fetch_all, insert

CONTENT = """
soul_mate 是一个桌面 AI 助手，搭载遐蝶人格。

遐蝶喜欢制作羊毛毡玩偶，也喜欢看书。
"""


class FakeEmbedder:
    """固定维度的假嵌入器，避免测试加载真模型"""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim

    async def encode(self, texts) -> list[list[float]]:
        return [[0.1] * self.dim for _ in texts]

    async def encode_one(self, text: str) -> list[float]:
        return [0.1] * self.dim


class FakeCollection:
    """内存版 documents collection，避免测试依赖真 Chroma"""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, dict]] = {}

    def upsert(self, ids, embeddings, documents, metadatas) -> None:
        for cid, doc, meta in zip(ids, documents, metadatas):
            self.rows[cid] = (doc, meta)

    def delete(self, ids=None, where=None) -> None:
        for cid in ids or []:
            self.rows.pop(cid, None)

    def update(self, ids, metadatas=None, documents=None) -> None:
        # Chroma 的 update 是整份 metadata 替换，不是合并
        for cid, meta in zip(ids, metadatas or []):
            if cid not in self.rows:
                continue
            doc, _ = self.rows[cid]
            self.rows[cid] = (doc, meta)

    def query(self, query_embeddings, n_results, include, where=None) -> dict:
        items = list(self.rows.items())[:n_results]
        return {
            "ids": [[cid for cid, _ in items]],
            "documents": [[doc for _, (doc, _) in items]],
            "metadatas": [[meta for _, (_, meta) in items]],
            "distances": [[0.1 for _ in items]],
        }


@pytest.fixture
def fake_collection(monkeypatch) -> FakeCollection:
    fake = FakeCollection()
    monkeypatch.setattr("app.memory.documents.get_documents_collection", lambda s: fake)
    monkeypatch.setattr("app.memory.rag.get_documents_collection", lambda s: fake)
    return fake


async def test_import_text_dedupes_same_content(initialized_db, fake_collection):
    first = await import_text(
        initialized_db, title="遐蝶设定", content=CONTENT, embedder=FakeEmbedder()
    )
    second = await import_text(
        initialized_db, title="遐蝶设定", content=CONTENT, embedder=FakeEmbedder()
    )

    assert first.chunk_count == 1
    assert second.id == first.id
    assert len(await fetch_all(initialized_db, "SELECT id FROM documents")) == 1
    assert len(fake_collection.rows) == 1


async def test_failed_import_does_not_block_reimport(initialized_db, fake_collection):
    """嵌入失败要把半成品回滚干净，不能留下挡住重导的空壳文档。"""

    class BoomEmbedder:
        async def encode(self, texts):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await import_text(
            initialized_db, title="遐蝶设定", content=CONTENT, embedder=BoomEmbedder()
        )

    # documents 行与 chunks 必须一起回滚，否则文档列表里会多出一个空壳文档
    assert await fetch_all(initialized_db, "SELECT id FROM documents") == []
    assert await fetch_all(initialized_db, "SELECT id FROM document_chunks") == []

    doc = await import_text(
        initialized_db, title="遐蝶设定", content=CONTENT, embedder=FakeEmbedder()
    )
    assert doc.chunk_count == 1
    assert len(await fetch_all(initialized_db, "SELECT id FROM documents")) == 1


async def test_reimport_after_soft_delete(initialized_db, fake_collection):
    first = await import_text(
        initialized_db, title="遐蝶设定", content=CONTENT, embedder=FakeEmbedder()
    )
    assert await delete_document(initialized_db, first.id) is True

    again = await import_text(
        initialized_db, title="遐蝶设定", content=CONTENT, embedder=FakeEmbedder()
    )
    assert again.id != first.id
    rows = await fetch_all(initialized_db, "SELECT status FROM documents")
    assert sorted(r["status"] for r in rows) == ["active", "deleted"]


async def test_unique_index_blocks_duplicate_active_hash(initialized_db):
    now = "2026-09-28T00:00:00+00:00"
    sql = (
        "INSERT INTO documents(id, title, size_bytes, chunk_count, content_hash, "
        "status, created_at, updated_at) VALUES (?, ?, 0, 1, ?, ?, ?, ?)"
    )
    await insert(initialized_db, sql, ("d1", "t1", "hash-1", "active", now, now))

    with pytest.raises(aiosqlite.IntegrityError):
        await insert(initialized_db, sql, ("d2", "t2", "hash-1", "active", now, now))

    # 软删之后同一份内容可以重新导入
    await execute(
        initialized_db, "UPDATE documents SET status = 'deleted' WHERE id = ?", ("d1",)
    )
    await insert(initialized_db, sql, ("d3", "t3", "hash-1", "active", now, now))


async def test_retrieve_documents_dedupes_and_keeps_top_k(initialized_db, fake_collection):
    """候选里去重后仍要凑够 top_k 条，不能少于 top_k。"""

    texts = ["遐蝶喜欢羊毛毡", "遐蝶喜欢看书", "遐蝶是死荫的侍女"]
    for i, text in enumerate(texts * 2):  # 每条重复两次，模拟不同文档里的相同片段
        fake_collection.rows[f"c{i}"] = (
            text,
            {"document_id": f"doc{i}", "chunk_index": i, "title": "t", "source": ""},
        )

    chunks = await retrieve_documents(
        initialized_db, "遐蝶喜欢什么", top_k=2, embedder=FakeEmbedder()
    )

    assert len(chunks) == 2
    assert len({c.content for c in chunks}) == 2


async def test_restore_soft_deleted_document(initialized_db, fake_collection):
    """恢复软删的文档：状态回到 active，向量也要真的写回索引。"""

    doc = await import_text(
        initialized_db, title="遐蝶设定", content=CONTENT, embedder=FakeEmbedder()
    )
    assert await delete_document(initialized_db, doc.id) is True
    assert fake_collection.rows == {}

    restored = await update_document(
        initialized_db, doc.id, status="active", embedder=FakeEmbedder()
    )

    assert restored is not None
    assert restored.status == "active"
    # 向量必须回来，否则就是「active 却检索不到」的空壳
    assert len(fake_collection.rows) == doc.chunk_count
    assert await count_documents(initialized_db) == 1


async def test_restore_conflicts_with_existing_active_hash(
    initialized_db, fake_collection
):
    """同内容的文档被重新导入过之后，恢复旧的那条要报冲突，而不是 500。"""

    first = await import_text(
        initialized_db, title="遐蝶设定", content=CONTENT, embedder=FakeEmbedder()
    )
    assert await delete_document(initialized_db, first.id) is True
    again = await import_text(
        initialized_db, title="遐蝶设定", content=CONTENT, embedder=FakeEmbedder()
    )
    assert again.id != first.id

    with pytest.raises(DocumentConflictError):
        await update_document(
            initialized_db, first.id, status="active", embedder=FakeEmbedder()
        )

    # 冲突被挡下之后，旧的那条必须原样留在 deleted
    rows = await fetch_all(
        initialized_db, "SELECT status FROM documents WHERE id = ?", (first.id,)
    )
    assert rows[0]["status"] == "deleted"


async def test_restore_refused_when_embedder_unavailable(
    initialized_db, fake_collection
):
    """嵌入模型不可用时拒绝恢复，不能留下「active 但没有向量」的空壳。"""

    class UnavailableEmbedder(FakeEmbedder):
        unavailable_reason = "模型目录不存在"

    doc = await import_text(
        initialized_db, title="遐蝶设定", content=CONTENT, embedder=FakeEmbedder()
    )
    assert await delete_document(initialized_db, doc.id) is True

    with pytest.raises(DocumentReindexError):
        await update_document(
            initialized_db,
            doc.id,
            status="active",
            embedder=UnavailableEmbedder(),
        )

    rows = await fetch_all(
        initialized_db, "SELECT status FROM documents WHERE id = ?", (doc.id,)
    )
    assert rows[0]["status"] == "deleted"
    assert fake_collection.rows == {}


async def test_rename_document_syncs_chunk_metadata(initialized_db, fake_collection):
    """改标题要把新标题同步进索引，检索拼 prompt 用的就是 metadata 里的标题。"""

    doc = await import_text(
        initialized_db, title="旧名字", content=CONTENT, embedder=FakeEmbedder()
    )

    # 故意不传 embedder：改标题不该依赖嵌入模型
    updated = await update_document(initialized_db, doc.id, title="新名字")

    assert updated is not None
    assert updated.title == "新名字"
    metas = [meta for _, (_, meta) in fake_collection.rows.items()]
    assert metas
    assert all(m["title"] == "新名字" for m in metas)
