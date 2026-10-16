"""SQLite 初始化与写操作测试"""

import aiosqlite
import pytest

from app.config import load_settings
from app.storage.db_SQlite import (
    execute,
    execute_many,
    fetch_all,
    fetch_one,
    health_check,
    init_db,
    insert,
    insert_with_rowcount,
)


async def test_health_check_ok(initialized_db):
    result = await health_check(initialized_db)
    assert result["status"] == "ok"
    assert result["schema_version"] == "1"


async def test_health_check_missing_file(tmp_settings, tmp_path):
    # 不初始化，直接查
    result = await health_check(tmp_settings)
    assert result["status"] == "error"


async def test_init_db_rejects_schema_version_mismatch(tmp_path, monkeypatch):
    """库里的 schema 版本与配置不一致时必须报错，不能被静默当成新库使用。"""

    monkeypatch.setenv("SOUL_MATE_PATHS__DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUL_MATE_LOGGING__CONSOLE", "false")

    await init_db(load_settings())

    monkeypatch.setenv("SOUL_MATE_SCHEMA_VERSION", "2")
    with pytest.raises(RuntimeError, match="schema 版本不一致"):
        await init_db(load_settings())


async def test_init_db_migrates_old_documents_table(tmp_path, monkeypatch):
    """老库的 documents 没有 content_hash：init_db 必须补列并建出唯一索引。"""

    monkeypatch.setenv("SOUL_MATE_PATHS__DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUL_MATE_LOGGING__CONSOLE", "false")
    settings = load_settings()

    db_path = settings.paths.sqlite_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 模拟老库：建 documents 时还没有 content_hash 列
    async with aiosqlite.connect(db_path) as conn:
        await conn.executescript(
            "CREATE TABLE documents ("
            "  id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT, mime_type TEXT,"
            "  size_bytes INTEGER NOT NULL DEFAULT 0,"
            "  chunk_count INTEGER NOT NULL DEFAULT 0,"
            "  status TEXT NOT NULL DEFAULT 'active',"
            "  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
            "INSERT INTO documents(id, title, size_bytes, status, created_at, updated_at)"
            "  VALUES ('old1', '旧文档', 10, 'active', '2026-09-28', '2026-09-28');"
        )
        await conn.commit()

    # 迁移要能重复调用
    await init_db(settings)
    await init_db(settings)

    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute("PRAGMA table_info(documents)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        async with conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_documents_content_hash",),
        ) as cur:
            index_row = await cur.fetchone()
        async with conn.execute(
            "SELECT content_hash FROM documents WHERE id = ?", ("old1",)
        ) as cur:
            old_row = await cur.fetchone()

    assert "content_hash" in cols
    assert index_row is not None
    assert old_row is not None
    assert old_row[0] is None


async def test_init_db_migrates_old_messages_table(tmp_path, monkeypatch):
    """老库的 messages 没有 tool_calls / status：init_db 必须补列且可重复调用。"""

    monkeypatch.setenv("SOUL_MATE_PATHS__DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SOUL_MATE_LOGGING__CONSOLE", "false")
    settings = load_settings()

    db_path = settings.paths.sqlite_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 模拟老库：messages 还是加"工具调用"两列之前的结构
    async with aiosqlite.connect(db_path) as conn:
        await conn.executescript(
            "CREATE TABLE messages ("
            "  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL,"
            "  content TEXT NOT NULL, tool_call_id TEXT, name TEXT,"
            "  created_at TEXT NOT NULL,"
            "  tokens_in INTEGER NOT NULL DEFAULT 0,"
            "  tokens_out INTEGER NOT NULL DEFAULT 0, model TEXT);"
        )
        await conn.commit()

    await init_db(settings)
    await init_db(settings)

    async with aiosqlite.connect(db_path) as conn:
        async with conn.execute("PRAGMA table_info(messages)") as cur:
            cols = {row[1] for row in await cur.fetchall()}

    assert {"tool_calls", "status"} <= cols


async def test_insert_and_fetch(initialized_db):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await insert(
        initialized_db,
        "INSERT INTO sessions(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("s1", "测试会话", now, now),
    )
    row = await fetch_one(initialized_db, "SELECT * FROM sessions WHERE id = ?", ("s1",))
    assert row is not None
    assert row["title"] == "测试会话"


async def test_execute_update_rowcount(initialized_db):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    await insert(
        initialized_db,
        "INSERT INTO sessions(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("s2", "a", now, now),
    )
    # 命中
    affected = await execute(
        initialized_db,
        "UPDATE sessions SET title = ? WHERE id = ?",
        ("b", "s2"),
    )
    assert affected == 1
    # 未命中
    affected = await execute(
        initialized_db,
        "UPDATE sessions SET title = ? WHERE id = ?",
        ("c", "nope"),
    )
    assert affected == 0


async def test_insert_or_ignore_rowcount(initialized_db):
    lastrowid, rowcount = await insert_with_rowcount(
        initialized_db,
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("schema_version", "1"),
    )
    # 已存在，rowcount 0
    assert rowcount == 0


async def test_execute_many(initialized_db):
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    rows = [
        ("m1", "s3", "user", "a", now),
        ("m2", "s3", "assistant", "b", now),
    ]
    # 先建 session，外键约束
    await insert(
        initialized_db,
        "INSERT INTO sessions(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("s3", "批量", now, now),
    )
    affected = await execute_many(
        initialized_db,
        "INSERT INTO messages(id, session_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    assert affected == 2
    result = await fetch_all(initialized_db, "SELECT * FROM messages WHERE session_id = ?", ("s3",))
    assert len(result) == 2
