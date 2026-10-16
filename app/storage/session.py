from __future__ import annotations

from app.config import Settings
from app.events import now_iso
from app.storage.db_SQlite import execute, insert_with_rowcount


async def ensure_session(settings: Settings, session_id: str) -> None:
    """确保会话存在，并刷新 updated_at 可重复调用"""

    now = now_iso()
    await insert_with_rowcount(
        settings,
        "INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (session_id, None, now, now),
    )
    await execute(
        settings,
        "UPDATE sessions SET updated_at = ? WHERE id = ?",
        (now, session_id),
    )
