"""documents 路由层测试（走 HTTP）

服务层的用例在 test_documents.py；这一份补路由层，因为它挡得住只有真打接口
才会暴露的问题——加 PATCH 时就踩过一次：api/documents.py 的 import 块被重写时
漏了 count_documents，GET /documents 会直接 NameError→500，服务层用例全绿也发现不了。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.memory.documents import import_text

CONTENT = """路由层测试用的文档内容。

第二段，用来验证切分。
"""


class FakeEmbedder:
    """固定维度的假嵌入器，避免测试加载真模型"""

    def __init__(self, dim: int = 8) -> None:
        self.dim = dim
        self.unavailable_reason: str | None = None

    async def encode(self, texts) -> list[list[float]]:
        return [[0.1] * self.dim for _ in texts]

    async def encode_one(self, text: str) -> list[float]:
        return [0.1] * self.dim


class FakeCollection:
    """内存版 documents collection"""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, dict]] = {}

    def upsert(self, ids, embeddings, documents, metadatas) -> None:
        for cid, doc, meta in zip(ids, documents, metadatas):
            self.rows[cid] = (doc, meta)

    def delete(self, ids=None, where=None) -> None:
        for cid in ids or []:
            self.rows.pop(cid, None)

    def update(self, ids, metadatas=None, documents=None) -> None:
        for cid, meta in zip(ids, metadatas or []):
            if cid in self.rows:
                self.rows[cid] = (self.rows[cid][0], meta)


@pytest.fixture
def documents_api(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """
    和 conftest.tmp_env 同样的隔离：数据目录指向 tmp_path、功能开关用环境变量盖掉，
    只把 features.rag 打开（documents 接口要求它）。

    嵌入模型不配路径，让 lifespan 自己降级；documents 里的 get_embedder 换成假对象。
    """

    monkeypatch.setenv("SOUL_MATE_PATHS__DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUL_MATE_APP__ENV", "test")
    monkeypatch.setenv("SOUL_MATE_LOGGING__CONSOLE", "false")
    monkeypatch.setenv("SOUL_MATE_FEATURES__RAG", "true")
    # 别让 lifespan 去加载真嵌入模型：它要几 GB 内存 + 几十秒，而这里每个用例
    # 都会新建一次 TestClient（= 跑一次 lifespan）。指到不存在的路径 →
    # ensure_configured 快速失败 → 记忆降级，文档接口照常。
    monkeypatch.setenv("SOUL_MATE_PATHS__EMBED_MODEL_DIR", str(tmp_path / "no-model"))
    monkeypatch.setenv("SOUL_MATE_FEATURES__MEMORY", "false")
    monkeypatch.setenv("SOUL_MATE_FEATURES__TOOLS", "false")
    monkeypatch.setenv("SOUL_MATE_FEATURES__TTS", "false")

    fake = FakeCollection()
    monkeypatch.setattr(
        "app.memory.documents.get_documents_collection", lambda s: fake
    )
    monkeypatch.setattr("app.memory.rag.get_documents_collection", lambda s: fake)
    monkeypatch.setattr(
        "app.memory.documents.get_embedder", lambda s: FakeEmbedder()
    )

    from app.main import app
    from app.memory.embedder import reset_embedder

    with TestClient(app) as client:
        client.fake_documents = fake  # type: ignore[attr-defined]
        yield client

    reset_embedder()


def _settings(client: TestClient):
    return client.app.state.settings  # type: ignore[attr-defined]


async def _seed(client: TestClient, title: str = "遐蝶设定") -> str:
    doc = await import_text(
        _settings(client), title=title, content=CONTENT, embedder=FakeEmbedder()
    )
    return doc.id


async def test_list_documents_over_http(documents_api: TestClient) -> None:
    """GET /documents 必须真的能返回（漏 import 那次就是在这里炸的）。"""

    await _seed(documents_api)

    res = documents_api.get("/documents")

    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "遐蝶设定"


async def test_patch_renames_document(documents_api: TestClient) -> None:
    doc_id = await _seed(documents_api, title="旧名字")

    res = documents_api.patch(f"/documents/{doc_id}", json={"title": "新名字"})

    assert res.status_code == 200
    assert res.json()["title"] == "新名字"
    assert res.json()["status"] == "active"


async def test_patch_soft_delete_then_restore(documents_api: TestClient) -> None:
    doc_id = await _seed(documents_api)

    assert documents_api.delete(f"/documents/{doc_id}").status_code == 200
    assert documents_api.get("/documents").json()["total"] == 0

    res = documents_api.patch(f"/documents/{doc_id}", json={"status": "active"})

    assert res.status_code == 200
    assert res.json()["status"] == "active"
    assert documents_api.get("/documents").json()["total"] == 1


async def test_patch_rejects_empty_body_and_unknown_id(
    documents_api: TestClient,
) -> None:
    doc_id = await _seed(documents_api)

    assert documents_api.patch(f"/documents/{doc_id}", json={}).status_code == 400
    assert (
        documents_api.patch("/documents/does-not-exist", json={"title": "x"}
                            ).status_code
        == 404
    )


async def test_patch_rejects_archived_status(documents_api: TestClient) -> None:
    """archived 是 worldbook / memory 的取值；documents 不认识，必须是 422。"""

    doc_id = await _seed(documents_api)

    res = documents_api.patch(f"/documents/{doc_id}", json={"status": "archived"})

    assert res.status_code == 422


async def test_patch_restore_conflict_returns_409(documents_api: TestClient) -> None:
    doc_id = await _seed(documents_api)
    assert documents_api.delete(f"/documents/{doc_id}").status_code == 200
    # 软删之后重新导入同一份内容：新文档占住了这条 content_hash
    again_id = await _seed(documents_api)
    assert again_id != doc_id

    res = documents_api.patch(f"/documents/{doc_id}", json={"status": "active"})

    assert res.status_code == 409
    assert "冲突" in res.json()["detail"]


async def test_patch_restore_returns_503_when_embedder_unavailable(
    documents_api: TestClient, monkeypatch
) -> None:
    """嵌入模型不可用时恢复要明确拒绝，而不是留下 active 却没有向量的空壳。"""

    class UnavailableEmbedder(FakeEmbedder):
        # 必须写在 __init__ 里：FakeEmbedder.__init__ 也设了同名实例属性，
        # 光写类属性会被它盖住，用例就悄悄变成「可用」了
        def __init__(self) -> None:
            super().__init__()
            self.unavailable_reason = "模型目录不存在"

    doc_id = await _seed(documents_api)
    assert documents_api.delete(f"/documents/{doc_id}").status_code == 200
    monkeypatch.setattr(
        "app.memory.documents.get_embedder", lambda s: UnavailableEmbedder()
    )

    res = documents_api.patch(f"/documents/{doc_id}", json={"status": "active"})

    assert res.status_code == 503
    assert documents_api.get("/documents").json()["total"] == 0
