"""设置面板接口（/config/public、PATCH /config）与配置合并顺序测试

注意：这些用例会把 overrides 文件指到 tmp_path，绝不写进项目目录。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import config as app_config
from app.config import load_settings
from app.config_schema import build_settings_payload


@pytest.fixture
def overrides_in_tmp(tmp_path: Path, monkeypatch) -> Path:
    """把 overrides 指到临时目录"""

    path = tmp_path / "config.overrides.yaml"
    monkeypatch.setattr(app_config, "CONFIG_OVERRIDES_FILE", path)
    return path


@pytest.fixture
def api(tmp_env: Path, overrides_in_tmp: Path, monkeypatch) -> Iterator[TestClient]:
    """带 lifespan 的 TestClient，overrides 指向 tmp

    这里自己建 TestClient 而不是复用 conftest 的 client fixture，是为了保证
    "改 overrides 路径" 一定发生在 lifespan（也就是 load_settings）之前。
    """

    from app.main import app

    assert overrides_in_tmp.parent == tmp_env
    with TestClient(app) as c:
        yield c


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------
# 配置合并顺序
# --------------------------------------------------------------------------


def test_overrides_win_over_local(tmp_path: Path, monkeypatch) -> None:
    """三个 YAML 的优先级：overrides > local > default"""

    monkeypatch.delenv("SOUL_MATE_COST__BUDGET", raising=False)
    _write(tmp_path / "config.default.yaml", "cost:\n  budget: 10.0\n")
    _write(tmp_path / "config.local.yaml", "cost:\n  budget: 20.0\n")
    _write(tmp_path / "config.overrides.yaml", "cost:\n  budget: 30.0\n")
    monkeypatch.chdir(tmp_path)

    assert load_settings().cost.budget == 30.0


def test_local_still_wins_over_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SOUL_MATE_COST__BUDGET", raising=False)
    _write(tmp_path / "config.default.yaml", "cost:\n  budget: 10.0\n")
    _write(tmp_path / "config.local.yaml", "cost:\n  budget: 20.0\n")
    monkeypatch.chdir(tmp_path)

    assert load_settings().cost.budget == 20.0


def test_list_is_replaced_not_concatenated(tmp_path: Path, monkeypatch) -> None:
    """deep_merge 对列表是整体替换

    config.default.yaml 里靠这个把 cors_origins 从 ["null"] 换成开发用的
    localhost 列表；如果哪天变成拼接，"null" 会一起留下，行为就变了。
    """

    monkeypatch.delenv("SOUL_MATE_SERVER__CORS_ORIGINS", raising=False)
    _write(
        tmp_path / "config.default.yaml",
        'server:\n  cors_origins:\n    - "null"\n',
    )
    _write(
        tmp_path / "config.local.yaml",
        "server:\n  cors_origins:\n    - http://localhost:5173\n",
    )
    monkeypatch.chdir(tmp_path)

    assert load_settings().server.cors_origins == ["http://localhost:5173"]


def test_removed_legacy_fields_do_not_break_startup(
    tmp_path: Path, monkeypatch
) -> None:
    """老配置里残留被删掉的死键时，仍然必须能启动

    这些键（tts.enabled / tools.enabled / rag.enabled / features.mcp /
    model.stream / tts.format / persona.enabled 等）已经删掉了，
    但用户手上的 config.local.yaml 里还会有，不能因此启动失败。
    """

    _write(
        tmp_path / "config.local.yaml",
        "features:\n"
        "  mcp: true\n"
        "  tasks: true\n"
        "tools:\n"
        "  enabled: true\n"
        "rag:\n"
        "  enabled: true\n"
        "tts:\n"
        "  enabled: true\n"
        "  format: wav\n"
        "model:\n"
        "  stream: false\n"
        "persona:\n"
        "  enabled: false\n"
        "memory:\n"
        "  audit_enabled: false\n"
        "embedding:\n"
        "  adapter: something\n"
        "agent:\n"
        "  graph: other_graph\n"
        "contracts:\n"
        "  tool_schema_version: 9\n",
    )
    monkeypatch.chdir(tmp_path)

    settings = load_settings()
    # 删掉的键被忽略，保留的字段仍是默认值
    assert settings.features.tts is False
    assert settings.tts.provider == "edge"
    # 顺手把"确实删掉了"钉住，避免以后有人又加回一个没人读的开关
    assert not hasattr(settings.model, "stream")
    assert not hasattr(settings.tts, "enabled")
    assert not hasattr(settings.tts, "format")
    assert not hasattr(settings.tools, "enabled")
    assert not hasattr(settings.rag, "enabled")
    assert not hasattr(settings.persona, "enabled")
    assert not hasattr(settings.agent, "graph")
    assert not hasattr(settings.memory, "audit_enabled")
    assert not hasattr(settings.memory, "summarize_on_session_end")
    assert not hasattr(settings.embedding, "adapter")
    assert not hasattr(settings.features, "mcp")
    assert not hasattr(settings.contracts, "tool_schema_version")


# --------------------------------------------------------------------------
# 白名单自检
# --------------------------------------------------------------------------


def test_every_writable_path_exists(tmp_settings) -> None:
    """白名单里的每个路径都必须真的取值到东西（防止拼错字段名）"""

    payload = build_settings_payload(tmp_settings)

    assert payload["groups"], "至少要有一个分组"
    for field in payload["fields"]:
        if field["kind"] == "secret":
            assert field["value"] is None
            assert isinstance(field["is_set"], bool)
            continue
        assert field["value"] is not None, f"{field['path']} 取不到值，字段名可能写错了"
        assert field["group"] in {g["id"] for g in payload["groups"]}


# --------------------------------------------------------------------------
# GET /config/public
# --------------------------------------------------------------------------


def test_public_config_has_settings_metadata(api: TestClient) -> None:
    body = api.get("/config/public").json()

    settings = body["settings"]
    assert settings["restart_required"] is True
    assert {g["id"] for g in settings["groups"]} >= {
        "voice",
        "model",
        "memory",
        "tools",
        "agent",
    }

    paths = {f["path"] for f in settings["fields"]}
    assert "features.tts" in paths
    assert "model.api_key" in paths


def test_public_config_never_leaks_api_key(api: TestClient) -> None:
    body = api.get("/config/public").json()

    secret_fields = [f for f in body["settings"]["fields"] if f["kind"] == "secret"]
    assert secret_fields, "API Key 应该是一个 secret 字段"
    for field in secret_fields:
        assert field["value"] is None


# --------------------------------------------------------------------------
# PATCH /config
# --------------------------------------------------------------------------


def test_patch_writes_overrides_and_reloads(
    api: TestClient, overrides_in_tmp: Path
) -> None:
    res = api.patch("/config", json={"values": {"memory.top_k": 7}})

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["updated"] == ["memory.top_k"]
    # 重建运行时就生效；memory.top_k 是请求期读取的，不需要重启
    assert body["restart_required"] is False
    assert body["config"]["settings"]

    written = yaml.safe_load(overrides_in_tmp.read_text(encoding="utf-8"))
    assert written == {"memory": {"top_k": 7}}

    # 响应里的值来自重读磁盘，已经是新值
    field = next(
        f
        for f in body["config"]["settings"]["fields"]
        if f["path"] == "memory.top_k"
    )
    assert field["value"] == 7


def test_patch_feature_switch_reports_restart_required(
    api: TestClient, overrides_in_tmp: Path
) -> None:
    """TTS 音色这类由 lifespan 装配的键，重建运行时不会重新装配，要提示重启

    注意不能用 features.tts 做样本：conftest 把它固定成了环境变量，
    PATCH 会被"环境变量锁定"拦下（那是另一条契约）。
    """

    res = api.patch("/config", json={"values": {"tts.voice": "zh-CN-YunxiNeural"}})

    assert res.status_code == 200
    assert res.json()["restart_required"] is True


def test_patch_reset_removes_key(
    api: TestClient, overrides_in_tmp: Path
) -> None:
    api.patch("/config", json={"values": {"memory.top_k": 7}})
    assert overrides_in_tmp.exists()

    res = api.patch("/config", json={"reset": ["memory.top_k"]})

    assert res.status_code == 200
    assert res.json()["reset"] == ["memory.top_k"]
    # 只剩这一个键，删掉后整个文件都不该留
    assert not overrides_in_tmp.exists()


def test_patch_rejects_empty_body(api: TestClient) -> None:
    assert api.patch("/config", json={}).status_code == 400


def test_patch_rejects_non_whitelisted_path(
    api: TestClient, overrides_in_tmp: Path
) -> None:
    res = api.patch("/config", json={"values": {"server.port": 1234}})

    assert res.status_code == 400
    assert "server.port" in res.json()["detail"]
    assert not overrides_in_tmp.exists()


def test_patch_rejects_out_of_range(api: TestClient) -> None:
    res = api.patch("/config", json={"values": {"memory.top_k": 999}})

    assert res.status_code == 400
    assert "每次回忆几条" in res.json()["detail"]


def test_patch_rejects_bad_enum(api: TestClient) -> None:
    res = api.patch("/config", json={"values": {"permission.default": "maybe"}})

    assert res.status_code == 400
    assert "每次询问" in res.json()["detail"]


def test_patch_rejects_wrong_type(api: TestClient) -> None:
    res = api.patch("/config", json={"values": {"agent.history_turns": "many"}})

    assert res.status_code == 400
    assert "整数" in res.json()["detail"]


def test_patch_body_rejects_unknown_keys(api: TestClient) -> None:
    res = api.patch("/config", json={"value": {"memory.top_k": 7}})

    assert res.status_code == 422


def test_patch_secret_is_written_but_never_echoed(
    api: TestClient, overrides_in_tmp: Path
) -> None:
    secret = "sk-test-only-not-a-real-key"

    res = api.patch("/config", json={"values": {"model.api_key": secret}})

    assert res.status_code == 200
    assert secret not in res.text, "响应里不能出现密钥原文"
    assert res.json()["updated"] == ["model.api_key"]
    assert res.json()["config"]["model"]["api_key_set"] is True

    written = yaml.safe_load(overrides_in_tmp.read_text(encoding="utf-8"))
    assert written == {"model": {"api_key": secret}}

    # 再读一次公开配置，依然只有布尔
    assert secret not in api.get("/config/public").text


def test_patch_secret_cannot_be_cleared(api: TestClient) -> None:
    res = api.patch("/config", json={"values": {"model.api_key": "  "}})

    assert res.status_code == 400


def test_patch_rejected_when_field_locked_by_env(
    api: TestClient, overrides_in_tmp: Path, monkeypatch
) -> None:
    """被环境变量固定的字段必须明确拒绝，而不是"保存成功但没生效" """

    monkeypatch.setenv("SOUL_MATE_MEMORY__TOP_K", "3")

    # 先在公开配置里标出来（value 来自进程启动时加载的实例，这里只校验锁定标记）
    fields = {
        f["path"]: f for f in api.get("/config/public").json()["settings"]["fields"]
    }
    assert fields["memory.top_k"]["locked_by"] == "SOUL_MATE_MEMORY__TOP_K"

    res = api.patch("/config", json={"values": {"memory.top_k": 7}})

    assert res.status_code == 400
    assert "SOUL_MATE_MEMORY__TOP_K" in res.json()["detail"]
    assert not overrides_in_tmp.exists()
def test_user_facing_text_has_no_developer_jargon(tmp_settings) -> None:
    """面向用户的文案里不许出现文件名、token、英文枚举值这类内部术语

    这个面板是给使用者看的。这条用例把要求钉住，避免以后随手加字段时又漏进来。
    """

    payload = build_settings_payload(tmp_settings)

    texts: list[str] = []
    for group in payload["groups"]:
        texts.append(group["title"])
        texts.append(group["description"])
    for item in payload["fields"]:
        texts.append(item["label"])
        texts.append(item["help"])
        texts.append(item["unit"])
        texts.extend(item["option_labels"].values())

    banned = [
        "yaml",
        "token",
        "base url",
        "read_file",
        "persona_first",
        "newest_first",
        "嵌入",
        "rag",
        "预算",
    ]

    for word in banned:
        for text in texts:
            assert word not in text.lower(), (
                f"面向用户的文案里出现了开发者术语 {word!r}：{text!r}"
            )
