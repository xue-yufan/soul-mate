from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

import aiosqlite

from app.config import Settings

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


async def get_conn(settings: Settings) -> aiosqlite.Connection:
    """创建一个新的 SQLite 异步连接"""

    """
    - timeout: Python 层等待锁释放的秒数
    - foreign_keys: 启用外键约束（连接级）
    - busy_timeout: SQLite 内部等待锁释放的毫秒数（连接级）
    - synchronous: WAL 模式下推荐 NORMAL（连接级）
    """

    conn = await aiosqlite.connect(
        settings.paths.sqlite_path,
        timeout=settings.database.timeout_s,
    )
    conn.row_factory = aiosqlite.Row

    busy_ms = int(settings.database.timeout_s * 1000)
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute(f"PRAGMA busy_timeout = {busy_ms}")
    await conn.execute("PRAGMA synchronous = NORMAL")

    return conn


@asynccontextmanager
async def connect(settings: Settings) -> AsyncIterator[aiosqlite.Connection]:
    """上下文管理器：自动 commit / rollback / close"""

    conn = await get_conn(settings)
    try:
        yield conn
        await conn.commit()
    except Exception:
        await conn.rollback()
        raise
    finally:
        await conn.close()


async def _ensure_column(
    conn: aiosqlite.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    """
    SQLite 的 ADD COLUMN 不支持 IF NOT EXISTS，手动检查

    新库走到这里时表还不存在（由随后的 schema.sql 创建），直接 ALTER 会
    no such table，所以表不存在就直接返回
    """

    async with conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ) as cur:
        if await cur.fetchone() is None:
            return

    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        cols = {row[1] for row in await cur.fetchall()}

    if column not in cols:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def init_db(settings: Settings) -> None:
    """
    创建父目录、设置 WAL、执行 schema.sql 可重复调用

    journal_mode = WAL 是持久化设置，只在首次或切换模式时真正生效
    需要独立 autocommit 连接，因为 PRAGMA journal_mode 不能在事务里
    """

    db_path = settings.paths.sqlite_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. 设置 WAL（持久化），isolation_level=None 表示 autocommit
    raw_conn = await aiosqlite.connect(db_path, isolation_level=None)
    try:
        async with raw_conn.execute("PRAGMA journal_mode = WAL") as cur:
            row = await cur.fetchone()
        mode = (row[0] if row else "").lower()
        if mode != "wal":
            raise RuntimeError(f"无法启用 WAL 模式，当前：{mode}")
    finally:
        await raw_conn.close()

    # 2. 应用 schema，并校验库中版本与配置一致
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    async with connect(settings) as conn:
        # 兼容迁移必须在 executescript 之前：schema.sql 里引用新列的索引
        # 在老库上会 no such column 直接失败（CREATE TABLE IF NOT EXISTS 不加列）
        await _ensure_column(conn, "documents", "content_hash", "TEXT")
        # 工具调用落库：assistant 行存 tool_calls，tool 行存结果状态
        await _ensure_column(conn, "messages", "tool_calls", "TEXT")
        await _ensure_column(conn, "messages", "status", "TEXT")
        await conn.executescript(schema)
        db_version = await _read_schema_version(conn)
        if db_version != settings.schema_version:
            raise RuntimeError(
                f"数据库 schema 版本不一致：{db_path} 中为 {db_version}，"
                f"而 config 中 schema_version 为 {settings.schema_version}。"
                "请迁移数据库，或修正 schema.sql 中写入的版本号后再启动。"
            )


async def _read_schema_version(conn: aiosqlite.Connection) -> int | None:
    """读取 meta.schema_version，缺失返回 None；非法值抛 RuntimeError"""

    async with conn.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        return None
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        raise RuntimeError(
            f"meta.schema_version 不是合法整数：{row['value']!r}"
        ) from None


async def health_check(settings: Settings) -> dict[str, Any]:
    """健康检查：查询 meta.schema_version"""

    try:
        async with connect(settings) as conn:
            async with conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ) as cur:
                row = await cur.fetchone()
            async with conn.execute("PRAGMA journal_mode") as cur:
                mode_row = await cur.fetchone()
        return {
            "status": "ok",
            "path": str(settings.paths.sqlite_path),
            "schema_version": row["value"] if row else None,
            "journal_mode": (mode_row[0] if mode_row else "").lower(),
        }
    except Exception as e:
        return {
            "status": "error",
            "path": str(settings.paths.sqlite_path),
            "message": str(e),
        }


# 重试兜底
_LOCKED_KEYWORDS = ("locked", "busy")


def _is_locked_error(e: BaseException) -> bool:
    msg = str(e).lower()
    return any(k in msg for k in _LOCKED_KEYWORDS)


async def _retry_on_locked(
    fn,
    *args,
    retries: int = 3,
    base_delay: float = 0.05,
    **kwargs,
):
    """
    捕获 locked/busy，指数退避异步重试

    busy_timeout 已经处理大部分锁等待，这里只兜底 SQLITE_BUSY_SNAPSHOT 等例外
    """

    last_err: BaseException | None = None
    for attempt in range(retries):
        try:
            return await fn(*args, **kwargs)
        except aiosqlite.OperationalError as e:
            if not _is_locked_error(e) or attempt == retries - 1:
                raise
            last_err = e
            await asyncio.sleep(base_delay * (2 ** attempt))
    if last_err is not None:
        raise last_err


async def fetch_one(settings: Settings, sql: str, params: Sequence = ()) -> aiosqlite.Row | None:
    """查询单行，返回 Row 或 None"""

    async with connect(settings) as conn:
        async with conn.execute(sql, params) as cur:
            return await cur.fetchone()


async def fetch_all(settings: Settings, sql: str, params: Sequence = ()) -> list[aiosqlite.Row]:
    """查询多行，返回 Row 列表"""

    async with connect(settings) as conn:
        async with conn.execute(sql, params) as cur:
            return await cur.fetchall()


async def _do_insert(settings, sql, params) -> int:
    async with connect(settings) as conn:
        async with conn.execute(sql, params) as cur:
            return cur.lastrowid


async def _do_insert_with_rowcount(settings, sql, params) -> tuple[int, int]:
    async with connect(settings) as conn:
        async with conn.execute(sql, params) as cur:
            return cur.lastrowid, cur.rowcount


async def _do_execute(settings, sql, params) -> int:
    async with connect(settings) as conn:
        async with conn.execute(sql, params) as cur:
            return cur.rowcount


async def _do_execute_many(settings, sql, seq_of_params) -> int:
    async with connect(settings) as conn:
        async with conn.executemany(sql, seq_of_params) as cur:
            return cur.rowcount


async def insert(settings: Settings, sql: str, params: Sequence = ()) -> int:
    """
    执行 INSERT，返回新记录的 lastrowid

    仅用于真正的 INSERT INSERT OR IGNORE 冲突被忽略时 lastrowid 不可信，
    请改用 insert_with_rowcount()
    """

    return await _retry_on_locked(_do_insert, settings, sql, params)


async def insert_with_rowcount(settings: Settings, sql: str, params: Sequence = ()) -> tuple[int, int]:
    """
    执行 INSERT，返回 (lastrowid, rowcount)

    rowcount == 0 表示 INSERT OR IGNORE 冲突被忽略
    """

    return await _retry_on_locked(_do_insert_with_rowcount, settings, sql, params)


async def execute(settings: Settings, sql: str, params: Sequence = ()) -> int:
    """
    执行 UPDATE / DELETE / DDL，返回受影响行数

    rowcount == 0 表示没有命中任何行
    """

    return await _retry_on_locked(_do_execute, settings, sql, params)


async def execute_many(settings: Settings, sql: str, seq_of_params: Sequence[Sequence]) -> int:
    """批量执行，返回受影响行数总和"""

    return await _retry_on_locked(_do_execute_many, settings, sql, seq_of_params)
