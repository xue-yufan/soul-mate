from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request

from app.agent.emotion import strip_emotion_prefix
from app.logging_config import get_logger
from app.storage.db_SQlite import execute, fetch_all

logger = get_logger("api.history")

router = APIRouter(prefix="/history", tags=["history"])


def _display_content(row) -> str:
    """
    返回给前端的正文

    模型偶尔把情绪标签写坏（</emom> 之类），脏标签会随回复一起落库；
    那是协议标记不是正文，展示之前清掉。用户消息原样返回——
    用户真这么打字也得照显。
    """

    content = row["content"]
    if row["role"] == "assistant":
        return strip_emotion_prefix(content)
    return content


@router.get("/messages")
async def list_messages(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """
    读取会话历史

    返回最近的 limit 条消息，按时间正序
    """

    settings = request.app.state.settings
    session_id = settings.agent.default_session_id

    # 先查总数
    count_rows = await fetch_all(
        settings,
        "SELECT COUNT(*) AS n FROM messages "
        "WHERE session_id = ? AND role IN ('user', 'assistant', 'tool')",
        (session_id,),
    )
    total = count_rows[0]["n"] if count_rows else 0

    # 倒序取 limit 条，再翻正
    rows = await fetch_all(
        settings,
        "SELECT id, role, content, created_at, "
        "tokens_in, tokens_out, model, "
        "tool_call_id, name, tool_calls, status "
        "FROM messages "
        "WHERE session_id = ? AND role IN ('user', 'assistant', 'tool') "
        "ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (session_id, limit, offset),
    )

    items = []
    for r in reversed(rows):
        item = {
            "id": r["id"],
            "role": r["role"],
            "content": _display_content(r),
            "created_at": r["created_at"],
            "tokens_in": r["tokens_in"] or 0,
            "tokens_out": r["tokens_out"] or 0,
            "model": r["model"] or "",
        }

        # assistant 的中间消息带 tool_calls（JSON 数组），前端据此把 tool 行
        # 关联成工具卡片
        if r["role"] == "assistant" and r["tool_calls"]:
            try:
                item["tool_calls"] = json.loads(r["tool_calls"])
            except json.JSONDecodeError:
                logger.warning(
                    "tool_calls 不是合法 JSON：message_id=%s", r["id"]
                )

        if r["role"] == "tool":
            item["tool_call_id"] = r["tool_call_id"]
            item["name"] = r["name"]
            item["status"] = r["status"]

        items.append(item)

    return {"total": total, "items": items}


@router.delete("/messages")
async def clear_messages(request: Request) -> dict:
    """
    清空当前会话的所有消息

    同时删除会话摘要，避免摘要里残留旧对话信息
    不影响记忆（memory_facts）和文档
    """

    settings = request.app.state.settings
    session_id = settings.agent.default_session_id

    affected = await execute(
        settings,
        "DELETE FROM messages WHERE session_id = ?",
        (session_id,),
    )

    await execute(
        settings,
        "DELETE FROM session_summaries WHERE session_id = ?",
        (session_id,),
    )

    logger.info("会话已清空：session=%s deleted=%d", session_id, affected)

    return {"ok": True, "deleted": affected}
