from __future__ import annotations

import json
from typing import Any

from app.config import Settings
from app.events import now_iso
from app.logging_config import get_logger
from app.storage.db_SQlite import fetch_all, fetch_one, insert

logger = get_logger("memory.audit")


async def log_event(
    settings: Settings,
    *,
    event_type: str,
    action: str,
    actor: str = "system",
    target_type: str | None = None,
    target_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    status: str = "ok",
) -> None:
    """
    写一条审计记录，失败只记日志，不抛

    event_type 区分来源：memory_fact（记忆增删改）/ tool_call（工具调用）。
    """

    try:
        await insert(
            settings,
            "INSERT INTO audit_log("
            "event_type, actor, target_type, target_id, action, "
            "before_json, after_json, status, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_type,
                actor,
                target_type,
                target_id,
                action,
                json.dumps(before, ensure_ascii=False) if before else None,
                json.dumps(after, ensure_ascii=False) if after else None,
                status,
                now_iso(),
            ),
        )
    except Exception:
        logger.exception(
            "审计写入失败：event_type=%s action=%s target_id=%s",
            event_type, action, target_id,
        )


async def log_change(
    settings: Settings,
    *,
    action: str,
    fact_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    actor: str = "system",
) -> None:
    """记忆变更的审计，log_event 的专用封装"""

    await log_event(
        settings,
        event_type="memory_fact",
        target_type="memory_fact",
        target_id=fact_id,
        action=action,
        actor=actor,
        before=before,
        after=after,
    )


def _audit_where(
    *,
    event_type: str | None,
    target_id: str | None,
) -> tuple[str, list[Any]]:
    """拼审计查询的 WHERE；event_type=None 表示不过滤"""

    sql = " FROM audit_log WHERE 1=1"
    params: list[Any] = []
    if event_type is not None:
        sql += " AND event_type = ?"
        params.append(event_type)
    if target_id:
        sql += " AND target_id = ?"
        params.append(target_id)
    return sql, params


async def list_audit(
    settings: Settings,
    *,
    event_type: str | None = "memory_fact",
    target_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """按条件查询审计记录；event_type=None 表示不过滤（记忆 / 工具调用都返回）"""

    where, params = _audit_where(event_type=event_type, target_id=target_id)

    rows = await fetch_all(
        settings,
        f"SELECT *{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        tuple([*params, limit, offset]),
    )
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "action": r["action"],
            "target_id": r["target_id"],
            "actor": r["actor"],
            "before": json.loads(r["before_json"]) if r["before_json"] else None,
            "after": json.loads(r["after_json"]) if r["after_json"] else None,
            "created_at": r["created_at"],
        })
    return result


async def count_audit(
    settings: Settings,
    *,
    event_type: str | None = "memory_fact",
    target_id: str | None = None,
) -> int:
    """按与 list_audit 相同的条件统计总数（分页用）"""

    where, params = _audit_where(event_type=event_type, target_id=target_id)

    row = await fetch_one(settings, f"SELECT COUNT(*) AS n{where}", tuple(params))
    return int(row["n"]) if row else 0
