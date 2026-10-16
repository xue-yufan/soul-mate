"""记忆 / 世界书的状态变更要同步到向量索引，否则归档的内容还会被召回"""

from __future__ import annotations

import pytest

from app.memory import documents, store, worldbook
from app.storage.db_SQlite import fetch_all


@pytest.fixture
def chroma_calls(monkeypatch):
    """把向量读写换成记录器，避免用例依赖 Chroma 与嵌入模型"""

    upserts: list[str] = []
    deletes: list[str] = []

    async def fake_upsert(settings, obj, embedder=None) -> None:
        upserts.append(obj.id)

    async def fake_delete(settings, obj_id) -> None:
        deletes.append(obj_id)

    monkeypatch.setattr(store, "_chroma_upsert", fake_upsert)
    monkeypatch.setattr(store, "_chroma_delete", fake_delete)
    monkeypatch.setattr(worldbook, "_chroma_upsert", fake_upsert)
    monkeypatch.setattr(worldbook, "_chroma_delete", fake_delete)
    return upserts, deletes


async def test_archived_fact_drops_vector_and_restore_reindexes(
    initialized_db, chroma_calls
) -> None:
    upserts, deletes = chroma_calls

    fact = await store.store_fact(
        initialized_db, type="fact", content="用户喜欢美式"
    )
    assert upserts == [fact.id]

    await store.update_fact(initialized_db, fact.id, status="archived")
    assert deletes == [fact.id]

    await store.update_fact(initialized_db, fact.id, status="active")
    assert upserts == [fact.id, fact.id]


async def test_archived_worldbook_entry_drops_vector_and_restore_reindexes(
    initialized_db, chroma_calls
) -> None:
    upserts, deletes = chroma_calls

    entry = await worldbook.store_entry(
        initialized_db, title="世界观", content="一座架空城市"
    )
    assert upserts == [entry.id]

    await worldbook.update_entry(initialized_db, entry.id, status="archived")
    assert deletes == [entry.id]

    await worldbook.update_entry(initialized_db, entry.id, status="active")
    assert upserts == [entry.id, entry.id]


async def test_failed_import_leaves_no_partial_rows(
    initialized_db, monkeypatch
) -> None:
    """嵌入失败要回滚 documents / document_chunks，别留一个空壳文档"""

    class BrokenEmbedder:
        async def encode(self, texts):
            raise RuntimeError("嵌入失败")

    class NoopCollection:
        def delete(self, *args, **kwargs) -> None:
            return None

    monkeypatch.setattr(
        documents, "get_embedder", lambda settings: BrokenEmbedder()
    )
    monkeypatch.setattr(
        documents, "get_documents_collection", lambda settings: NoopCollection()
    )

    with pytest.raises(RuntimeError):
        await documents.import_text(
            initialized_db, title="会失败的导入", content="正文内容。" * 20
        )

    assert await fetch_all(initialized_db, "SELECT id FROM documents") == []
    assert await fetch_all(initialized_db, "SELECT id FROM document_chunks") == []
