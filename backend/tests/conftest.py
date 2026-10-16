"""pytest 共享 fixture"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_settings
from app.lifecycle import Lifecycle
from app.storage.db_SQlite import init_db


@pytest.fixture
def tmp_env(tmp_path: Path, monkeypatch) -> Path:
    """
    把运行环境指向 tmp_path：数据目录隔离、控制台日志关闭。

    注意：sqlite 与日志路径都由 data_dir 派生（PathsConfig 里的 property），
    不存在 SOUL_MATE_PATHS__SQLITE_PATH 之类的配置项，设了也会被静默忽略。
    """

    monkeypatch.setenv("SOUL_MATE_PATHS__DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUL_MATE_APP__ENV", "test")
    monkeypatch.setenv("SOUL_MATE_LOGGING__CONSOLE", "false")
    # 测试不吃本地 config.local.yaml 的功能开关：
    # 开着 features.memory/rag 会让用例真加载嵌入模型、真起 Chroma，慢且依赖本机配置
    monkeypatch.setenv("SOUL_MATE_FEATURES__TOOLS", "false")
    monkeypatch.setenv("SOUL_MATE_FEATURES__MEMORY", "false")
    monkeypatch.setenv("SOUL_MATE_FEATURES__RAG", "false")
    monkeypatch.setenv("SOUL_MATE_FEATURES__TTS", "false")

    # 嵌入模型默认指到不存在的地方：涉及嵌入的用例会自动跳过，整轮测试几秒到一分钟。
    # 想真跑它们（会加载 torch + 几百 MB 模型，整轮慢好几分钟）：
    #     $env:SOUL_MATE_TEST_REAL_EMBED = "1"
    if not os.environ.get("SOUL_MATE_TEST_REAL_EMBED"):
        monkeypatch.setenv(
            "SOUL_MATE_PATHS__EMBED_MODEL_DIR", str(tmp_path / "no-embed-model")
        )
    return tmp_path


@pytest.fixture
def tmp_settings(tmp_env: Path) -> Settings:
    """按隔离后的环境加载一份 Settings。"""

    return load_settings()


@pytest.fixture
async def initialized_db(tmp_settings: Settings) -> Settings:
    """初始化数据库后返回 settings。"""

    await init_db(tmp_settings)
    return tmp_settings


@pytest.fixture
def client(tmp_env: Path) -> Iterator[TestClient]:
    """
    带 lifespan 的 TestClient。

    lifespan 启动时会按当前（已被 monkeypatch 的）环境重新加载配置，
    因此数据目录与日志都落在 tmp_path 下，不会写进项目的 ./data。
    """

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
async def lifecycle() -> Lifecycle:
    """一个新的 Lifecycle，版本固定 1。"""
    return Lifecycle(version=1, shutdown_timeout=1.0, ws_notify_delay=0.0)
