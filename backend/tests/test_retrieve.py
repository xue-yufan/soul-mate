"""合并召回的开关门控与条数上限测试（不加载真模型 / 真 Chroma）"""

from __future__ import annotations

import app.memory.retrieve as retrieve
from app.memory.retrieve import recall, recall_all
from app.memory.store import MemoryFact


class FakeEmbedder:
    async def encode_one(self, text: str) -> list[float]:
        return [0.1]

    async def encode(self, texts) -> list[list[float]]:
        return [[0.1] for _ in texts]


class UnavailableEmbedder:
    """模拟预热失败的嵌入模型：只暴露不可用原因"""

    unavailable_reason = "模型文件缺失"

    async def encode(self, texts):
        raise AssertionError("嵌入模型不可用时不应真的去编码")


async def test_recall_all_skips_when_embedder_unavailable(tmp_settings, monkeypatch):
    """嵌入模型不可用时直接跳过召回，不再逐个来源重试"""

    settings = _settings(tmp_settings, memory=True, rag=True)

    called = False

    async def fake_similar(settings, query, *, top_k=20, embedder=None):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(retrieve, "search_similar", fake_similar)

    items = await recall_all(settings, "喜欢什么", embedder=UnavailableEmbedder())

    assert items == []
    assert called is False


def _fact(i: int) -> MemoryFact:
    ts = "2026-09-01T00:00:00+00:00"
    return MemoryFact(
        id=f"f{i}",
        type="preference",
        content=f"记忆 {i}",
        importance=0.9,
        source=None,
        status="active",
        created_at=ts,
        updated_at=ts,
    )


def _item(kind: str, i: int) -> retrieve.RecallItem:
    # 同组内分数要有区分度，否则 min-max 归一后同组分值全部相等，排序结果不稳定
    score = 1.0 - 0.1 * i
    return retrieve.RecallItem(
        kind=kind,  # type: ignore[arg-type]
        title=f"{kind}-{i}",
        content=f"{kind} 内容 {i}",
        score=score,
        raw_score=score,
        meta={},
    )


def install_fakes(
    monkeypatch,
    *,
    memories: int = 1,
    documents: int = 1,
    worldbooks: int = 0,
) -> None:
    """把三路召回的底层检索换成假的，隔离模型与 Chroma"""

    async def fake_similar(settings, query, *, top_k=20, embedder=None):
        return [(f"f{i}", 0.1) for i in range(memories)]

    async def fake_facts(settings, fact_ids):
        return [_fact(i) for i in range(len(fact_ids))]

    async def fake_documents(settings, query, *, top_k=None, embedder=None):
        return [_item("document", i) for i in range(documents)]

    async def fake_worldbook(settings, query, *, top_k=None, embedder=None):
        return [_item("worldbook", i) for i in range(worldbooks)]

    monkeypatch.setattr(retrieve, "search_similar", fake_similar)
    monkeypatch.setattr(retrieve, "get_facts_by_ids", fake_facts)
    monkeypatch.setattr(retrieve, "recall_documents", fake_documents)
    monkeypatch.setattr(retrieve, "recall_worldbook", fake_worldbook)


def _settings(tmp_settings, *, memory: bool, rag: bool, memory_top_k: int = 10):
    settings = tmp_settings.model_copy(deep=True)
    settings.features.memory = memory
    settings.features.rag = rag
    settings.memory.top_k = memory_top_k
    return settings


async def test_recall_returns_empty_when_memory_disabled(tmp_settings, monkeypatch):
    install_fakes(monkeypatch)
    settings = _settings(tmp_settings, memory=False, rag=True)

    assert await recall(settings, "喜欢什么", embedder=FakeEmbedder()) == []


async def test_recall_all_skips_memory_when_feature_off(tmp_settings, monkeypatch):
    """rag-only 时不能把记忆塞进 prompt"""

    install_fakes(monkeypatch, memories=1, documents=1)
    settings = _settings(tmp_settings, memory=False, rag=True)

    items = await recall_all(settings, "喜欢什么", embedder=FakeEmbedder())

    assert [i.kind for i in items] == ["document"]


async def test_recall_all_keeps_documents_when_memory_top_k_zero(
    tmp_settings, monkeypatch
):
    """memory.top_k=0 不该把文档召回一起丢掉"""

    install_fakes(monkeypatch, documents=1)
    settings = _settings(tmp_settings, memory=False, rag=True, memory_top_k=0)

    items = await recall_all(settings, "喜欢什么", embedder=FakeEmbedder())

    assert [i.kind for i in items] == ["document"]


async def test_recall_all_cap_is_sum_of_enabled_sources(tmp_settings, monkeypatch):
    install_fakes(monkeypatch, memories=2, documents=2, worldbooks=2)
    settings = _settings(tmp_settings, memory=True, rag=True)
    settings.memory.top_k = 1
    settings.rag.top_k = 1
    settings.rag.worldbook_top_k = 1

    items = await recall_all(settings, "喜欢什么", embedder=FakeEmbedder())

    # 上限 = 1(记忆) + 1(文档) + 1(设定)
    assert len(items) == 3
    assert {i.kind for i in items} == {"memory", "document", "worldbook"}


async def test_recall_all_explicit_cap_is_respected(tmp_settings, monkeypatch):
    install_fakes(monkeypatch, memories=2, documents=2, worldbooks=2)
    settings = _settings(tmp_settings, memory=True, rag=True)

    items = await recall_all(
        settings, "喜欢什么", total_top_k=1, embedder=FakeEmbedder()
    )

    assert len(items) == 1
