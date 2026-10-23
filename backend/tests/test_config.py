"""配置加载与校验测试"""

import os

import pytest

from app.config import load_settings


@pytest.fixture(autouse=True)
def clear_soul_mate_env(monkeypatch):
    """清掉可能污染用例的 SOUL_MATE_* 环境变量。"""

    for key in list(os.environ):
        if key.startswith("SOUL_MATE_"):
            monkeypatch.delenv(key)


def test_code_defaults_without_yaml(tmp_path, monkeypatch):
    """读不到 YAML 时（例如不在项目根目录运行）走代码默认值。"""

    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.schema_version == 1
    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 8000
    assert settings.server.ws_path == "/ws"
    assert settings.contracts.ws_event_version == 1
    assert settings.features.memory is False
    assert settings.features.vision is True
    assert settings.vision.model == "qwen3-vl:4b"


def test_yaml_overrides_code_defaults(tmp_path, monkeypatch):
    """config.default.yaml 能覆盖代码默认值。"""

    (tmp_path / "config.default.yaml").write_text(
        "server:\n  port: 12345\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    assert load_settings().server.port == 12345


def test_local_yaml_deep_merges_default(tmp_path, monkeypatch):
    """config.local.yaml 只写要覆盖的字段，同段其它字段沿用 default。"""

    (tmp_path / "config.default.yaml").write_text(
        "server:\n  host: 10.0.0.9\n  port: 1111\n  ws_path: /default-ws\n",
        encoding="utf-8",
    )
    (tmp_path / "config.local.yaml").write_text(
        "server:\n  port: 2222\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    settings = load_settings()
    assert settings.server.port == 2222
    assert settings.server.host == "10.0.0.9"
    assert settings.server.ws_path == "/default-ws"


def test_env_overrides_yaml(tmp_path, monkeypatch):
    """环境变量优先级高于 YAML。"""

    (tmp_path / "config.default.yaml").write_text(
        "server:\n  port: 1111\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SOUL_MATE_SERVER__PORT", "9999")

    assert load_settings().server.port == 9999


def test_invalid_port_rejected(monkeypatch):
    monkeypatch.setenv("SOUL_MATE_SERVER__PORT", "70000")
    with pytest.raises(SystemExit):
        load_settings()


def test_secret_redacted(monkeypatch):
    monkeypatch.setenv("SOUL_MATE_MODEL__API_KEY", "sk-test-123")
    settings = load_settings()
    # SecretStr 打印时脱敏
    assert "sk-test-123" not in str(settings.model.api_key)
    assert settings.model.api_key.get_secret_value() == "sk-test-123"
