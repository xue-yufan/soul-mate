from __future__ import annotations

from app.config import Settings
from app.events import now_iso
from app.logging_config import get_logger
from app.models import ChatMessage, ModelAdapter
from app.storage.db_SQlite import execute, fetch_all, fetch_one

logger = get_logger("storage.history")


async def load_recent_messages(
    settings: Settings,
    session_id: str,
    *,
    max_turns: int,
    exclude_message_id: str | None = None,
) -> list[ChatMessage]:
    """
    加载最近 max_turns 轮的消息，按时间正序：

    只取 user / assistant，跳过 system / tool

    exclude_message_id：本轮 user 消息的落库 id。传入后该条不参与历史
    （节点会把它显式补回消息列表，避免同一句出现两次）。
    """

    if max_turns <= 0:
        return []

    limit = max_turns * 2
    rows = await fetch_all(
        settings,
        "SELECT role, content FROM messages "
        "WHERE session_id = ? AND role IN ('user','assistant') "
        "AND (? IS NULL OR id != ?) "
        "ORDER BY created_at DESC LIMIT ?",
        (session_id, exclude_message_id, exclude_message_id, limit),
    )
    # fetch_all 返回时间倒序，反转为正序
    rows = list(reversed(rows))
    return [
        ChatMessage(role=r["role"], content=r["content"])
        for r in rows
    ]


async def load_summary(
        settings: Settings,
        session_id: str,
) -> None | str:
    """加载会话摘要，没有则返回 None"""

    row = await fetch_one(
        settings,
        "SELECT summary FROM session_summaries WHERE session_id = ?",
        (session_id,),
    )
    return row["summary"] if row else None


def _estimate_token(text: str) -> int:
    """粗略估算 token 数：字符数 // 2"""

    return len(text) // 2


async def _build_summary(
    adapter: ModelAdapter,
    messages: list[ChatMessage]
) -> str:
    """调模型生成摘要，不注入人格"""

    if not messages:
        return ""

    transcript = "\n".join(
        f"{m.role}: {m.content}" for m in messages
    )
    prompt = (
        "请将以下对话压缩成简短摘要，保留关键信息、用户偏好、"
        "重要事实与未完成事项。用第三人称陈述，不超过 200 字。\n\n"
        f"{transcript}"
    )

    chunks: list[str] = []
    async for chunk in adapter.chat_stream([
        ChatMessage(role="user", content=prompt)
    ]):
        if chunk.delta:
            chunks.append(chunk.delta)

    return "".join(chunks).strip()


async def maybe_summarize(
    settings: Settings,
    session_id: str,
    adapter: ModelAdapter,
) -> None:
    """
    检查是否需要摘要

    触发条件：最近 history_turns 轮总 token 超过 summary_trigger_tokens
    摘要内容：除最近 summary_keep_recent_turns 轮外的旧消息
    （summary_keep_recent_turns 为 0 表示全部摘要）
    与已有摘要合并后写入 session_summaries
    """

    history_turns = settings.agent.history_turns
    trigger_tokens = settings.agent.summary_trigger_tokens
    keep_turns = settings.agent.summary_keep_recent_turns

    if history_turns <= 0 or trigger_tokens <= 0:
        return

    all_messages = await load_recent_messages(
        settings,
        session_id,
        max_turns=history_turns,
    )
    if not all_messages:
        return

    total_tokens = sum(_estimate_token(m.content) for m in all_messages)
    if total_tokens < trigger_tokens:
        return

    keep_count = max(keep_turns, 0) * 2
    if len(all_messages) <= keep_count:
        return

    # 注意：keep_count 为 0 时 [:-0] 是空切片，必须单独处理
    to_summarize = all_messages[:-keep_count] if keep_count else list(all_messages)
    if not to_summarize:
        return

    logger.info("触发摘要：session=%s，消息数=%d，待摘要 %d 条", session_id, len(all_messages), len(to_summarize))

    try:
        new_summary = await _build_summary(adapter, to_summarize)
    except Exception:
        logger.exception("摘要生成失败")
        return

    if not new_summary:
        return

    # 与已有摘要合并
    existing = await load_summary(settings, session_id)
    merged = f"{existing}\n\n{new_summary}" if existing else new_summary

    now = now_iso()
    await execute(
        settings,
        "INSERT INTO session_summaries(session_id, summary, updated_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(session_id) DO UPDATE SET "
        "summary = excluded.summary, "
        "updated_at = excluded.updated_at",
        (session_id, merged, now),
    )

    logger.info("摘要已更新：session=%s，%d 字", session_id, len(merged))
