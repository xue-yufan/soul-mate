"""/memory 接口：列表分页、编辑、软删恢复与审计

这些接口此前完全没有用例覆盖，于是藏着两处拼写错误（list_facts 收到
offse=、PATCH 判空读 body.importanceis）。这里把每个分支都走一遍。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.events import now_iso
from app.storage.db_SQlite import insert


@pytest.fixture
def memory_api(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """带 lifespan 的 TestClient：只开记忆模块，向量读写换成空实现

    嵌入模型目录指向不存在的路径，预热会立刻降级；再把 _chroma_* 换掉，
    用例就不必真的加载嵌入模型或起 Chroma。
    """

    monkeypatch.setenv("SOUL_MATE_PATHS__DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUL_MATE_APP__ENV", "test")
    monkeypatch.setenv("SOUL_MATE_LOGGING__CONSOLE", "false")
    monkeypatch.setenv("SOUL_MATE_FEATURES__MEMORY", "true")
    monkeypatch.setenv("SOUL_MATE_FEATURES__RAG", "false")
    monkeypatch.setenv("SOUL_MATE_FEATURES__TOOLS", "false")
    monkeypatch.setenv("SOUL_MATE_FEATURES__TTS", "false")
    monkeypatch.setenv(
        "SOUL_MATE_PATHS__EMBED_MODEL_DIR", str(tmp_path / "no-such-model")
    )

    async def _noop(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr("app.memory.store._chroma_upsert", _noop)
    monkeypatch.setattr("app.memory.store._chroma_delete", _noop)

    from app.main import app

    try:
        with TestClient(app) as client:
            yield client
    finally:
        # 预热失败会把全局单例标记为不可用，别让它漏给后面的用例
        from app.memory.embedder import reset_embedder

        reset_embedder()


async def _seed_fact(
    settings,
    fact_id: str,
    content: str,
    *,
    status: str = "active",
    importance: float = 0.5,
) -> None:
    now = now_iso()
    await insert(
        settings,
        "INSERT INTO memory_facts("
        "id, type, content, importance, source, status, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (fact_id, "fact", content, importance, None, status, now, now),
    )


async def test_list_facts_returns_real_total(memory_api: TestClient) -> None:
    settings = memory_api.app.state.settings
    for i in range(3):
        await _seed_fact(settings, f"f{i}", f"内容{i}")

    body = memory_api.get("/memory/facts", params={"limit": 1}).json()

    assert body["total"] == 3  # 不是本页条数
    assert len(body["items"]) == 1


async def test_patch_fact_importance_only(memory_api: TestClient) -> None:
    settings = memory_api.app.state.settings
    await _seed_fact(settings, "f1", "原来")

    res = memory_api.patch("/memory/facts/f1", json={"importance": 0.9})

    assert res.status_code == 200
    assert res.json()["importance"] == 0.9


async def test_patch_fact_status_only(memory_api: TestClient) -> None:
    settings = memory_api.app.state.settings
    await _seed_fact(settings, "f1", "原来")

    res = memory_api.patch("/memory/facts/f1", json={"status": "archived"})

    assert res.status_code == 200
    assert res.json()["status"] == "archived"


async def test_patch_fact_without_fields_is_rejected(
    memory_api: TestClient,
) -> None:
    settings = memory_api.app.state.settings
    await _seed_fact(settings, "f1", "原来")

    assert memory_api.patch("/memory/facts/f1", json={}).status_code == 400


async def test_soft_delete_then_restore(memory_api: TestClient) -> None:
    settings = memory_api.app.state.settings
    await _seed_fact(settings, "f1", "原来")

    assert memory_api.delete("/memory/facts/f1").status_code == 200
    assert memory_api.get("/memory/facts/f1").json()["status"] == "deleted"

    res = memory_api.post("/memory/facts/f1/restore")

    assert res.status_code == 200
    assert res.json()["status"] == "active"


async def test_audit_total_is_not_page_size(memory_api: TestClient) -> None:
    settings = memory_api.app.state.settings
    await _seed_fact(settings, "f1", "原来")
    memory_api.patch("/memory/facts/f1", json={"content": "第一次"})
    memory_api.patch("/memory/facts/f1", json={"content": "第二次"})

    body = memory_api.get("/memory/audit", params={"limit": 1}).json()

    assert body["total"] == 2
    assert len(body["items"]) == 1


async def test_audit_event_type_filter(memory_api: TestClient) -> None:
    """默认只看记忆；传 tool_call 只看工具；传 all 两种都看"""

    from app.memory.audit import log_event

    settings = memory_api.app.state.settings
    await _seed_fact(settings, "f1", "原来")
    memory_api.patch("/memory/facts/f1", json={"content": "改后"})

    await log_event(
        settings,
        event_type="tool_call",
        actor="assistant",
        target_type="tool",
        target_id="call_1",
        action="get_time",
        before={"args": {}},
        after={"status": "ok"},
        status="ok",
    )

    # 默认（不传 event_type）沿用原语义：只看记忆变更
    assert memory_api.get("/memory/audit").json()["total"] == 1

    tool = memory_api.get(
        "/memory/audit", params={"event_type": "tool_call"}
    ).json()
    assert tool["total"] == 1
    assert tool["items"][0]["action"] == "get_time"
    assert tool["items"][0]["target_id"] == "call_1"

    both = memory_api.get("/memory/audit", params={"event_type": "all"}).json()
    assert both["total"] == 2
async def test_list_facts_without_status_means_all(
    memory_api: TestClient,
) -> None:
    """不带 status 表示不过滤；空串也归一成不过滤

    前端的「全部」选项就是靠「不发这个参数」表达的，所以默认值必须是 None；
    空串则走 `status or None` 那条路——两者都别退化成 `WHERE status = ''`。
    """

    settings = memory_api.app.state.settings
    await _seed_fact(settings, "s1", "活跃的")
    await _seed_fact(settings, "s2", "归档的", status="archived")
    await _seed_fact(settings, "s3", "已删除的", status="deleted")

    # 不带 status：三条都要回来（旧行为是默认只给 active）
    body = memory_api.get("/memory/facts").json()
    assert body["total"] == 3
    assert len(body["items"]) == 3

    # 显式过滤照旧
    active = memory_api.get("/memory/facts", params={"status": "active"}).json()
    assert active["total"] == 1
    assert active["items"][0]["id"] == "s1"

    # 空串：当不过滤，且 total 不能和 items 打架
    blank = memory_api.get("/memory/facts", params={"status": ""}).json()
    assert blank["total"] == 3
    assert len(blank["items"]) == 3
