from __future__ import annotations

import json
import re
from typing import Any

from app.config import Settings
from app.logging_config import get_logger
from app.memory.embedder import Embedder, get_embedder
from app.memory.store import (
    MemoryFact,
    get_facts_by_ids,
    search_similar,
    store_fact,
    update_fact,
)
from app.models.base import ChatMessage, ModelAdapter

logger = get_logger("memory.extract")

# 去重阈值：cosine distance 越小越相似
# 0.15 大约对应相似度 0.85
DEDUP_DISTANCE_THRESHOLD = 0.15

VALID_TYPES = {"preference", "fact", "event", "emotion"}

# 注意：prompt 里 JSON 示例的花括号必须写成 {{ }}，
# 否则 str.format 会把 {"type": ...} 当成替换字段直接 KeyError
_EXTRACT_PROMPT = """\
你是一个记忆抽取器。从以下对话中提取用户值得长期记住的信息。

规则：
- 只提取用户偏好、事实、关键事件、情绪状态。
- 每条一句话，第三人称陈述，例如"用户喜欢喝美式"。
- 不确定或一次性的信息不要提取。
- 最多提取 3 条。
- 输出 JSON 数组，不要输出其他文字。
- 没有可提取的内容就输出 []。

格式：
[
  {{"type": "preference", "content": "用户喜欢喝美式", "importance": 0.7}},
  {{"type": "fact", "content": "用户的名字是小明", "importance": 0.9}}
]

type 只能是 preference / fact / event / emotion。
importance 范围 0.0 ~ 1.0，0.5 是默认值。

用户：{user_text}
助手：{assistant_text}

输出："""


def _strip_code_fence(text: str) -> str:
    """去掉模型可能加的 markdown 代码块围栏"""

    text = text.strip()
    # ```json ... ``` 或 ``` ... ```
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text


def _parse_facts(raw: str) -> list[dict[str, Any]]:
    """
    解析模型输出

    容错：
    - 带 markdown 围栏
    - 前后有多余文字（尝试提取第一个 [ ... ]）
    - 解析失败返回空列表
    """

    if not raw or not raw.strip():
        return []

    text = _strip_code_fence(raw)

    # 尝试直接解析
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试提取第一个 JSON 数组
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            logger.warning("抽取结果无法解析：%s", raw[:200])
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning("抽取结果无法解析：%s", raw[:200])
            return []

    if not isinstance(data, list):
        logger.warning("抽取结果不是数组：%s", raw[:200])
        return []

    result: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue

        type_ = item.get("type", "fact")
        if type_ not in VALID_TYPES:
            type_ = "fact"

        try:
            importance = float(item.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        importance = max(0.0, min(1.0, importance))

        result.append({
            "type": type_,
            "content": content.strip(),
            "importance": importance,
        })

    return result


async def extract_facts(
    adapter: ModelAdapter,
    user_text: str,
    assistant_text: str,
) -> list[dict[str, Any]]:
    """
    调模型抽取事实

    返回 [{"type": ..., "content": ..., "importance": ...}, ...]
    失败或无可抽取返回空列表
    """

    prompt = _EXTRACT_PROMPT.format(
        user_text=user_text.strip(),
        assistant_text=assistant_text.strip(),
    )

    chunks: list[str] = []
    try:
        async for chunk in adapter.chat_stream([
            ChatMessage(role="user", content=prompt)
        ]):
            if chunk.delta:
                chunks.append(chunk.delta)
    except Exception:
        logger.exception("抽取调模型失败")
        return []

    raw = "".join(chunks)
    facts = _parse_facts(raw)

    if facts:
        logger.debug("抽取到 %d 条候选事实", len(facts))
    return facts


async def _find_duplicate(
    settings: Settings,
    content: str,
    embedder: Embedder,
) -> MemoryFact | None:
    """
    查找与 content 语义重复的已有 fact

    距离小于 DEDUP_DISTANCE_THRESHOLD 视为重复
    返回最相似的那条，没有则 None
    """

    results = await search_similar(
        settings, content, top_k=3, embedder=embedder
    )
    if not results:
        return None

    fact_id, distance = results[0]
    if distance > DEDUP_DISTANCE_THRESHOLD:
        return None

    facts = await get_facts_by_ids(settings, [fact_id])
    return facts[0] if facts else None


async def extract_and_store(
    settings: Settings,
    adapter: ModelAdapter,
    user_text: str,
    assistant_text: str,
    *,
    embedder: Embedder | None = None,
) -> int:
    """
    抽取事实并写入 store。返回新增/更新的条数

    去重逻辑：
    - 语义重复时，按 conflict_policy 决定：
      - newest_first：用新内容覆盖旧条目
      - persona_first：保留旧条目，跳过
    - 不重复则新建
    """

    if not settings.memory.extract_per_turn:
        return 0

    facts = await extract_facts(adapter, user_text, assistant_text)
    if not facts:
        return 0

    emb = embedder or get_embedder(settings)
    policy = settings.memory.conflict_policy
    min_importance = settings.memory.min_importance
    count = 0

    for fact in facts:
        if fact["importance"] < min_importance:
            logger.debug("跳过低重要性事实：importance=%.2f content=%r", fact["importance"], fact["content"][:40])
            continue

        duplicate = await _find_duplicate(settings, fact["content"], emb)

        if duplicate is not None:
            if policy == "newest_first":
                await update_fact(
                    settings,
                    duplicate.id,
                    content=fact["content"],
                    importance=fact["importance"],
                    embedder=emb,
                )
                logger.info("记忆更新：id=%s content=%r", duplicate.id, fact["content"][:40])
                count += 1
            else:
                logger.debug("记忆已存在，跳过：content=%r", fact["content"][:40])
            continue

        await store_fact(
            settings,
            type=fact["type"],
            content=fact["content"],
            importance=fact["importance"],
            source="user_input",
            embedder=emb,
        )
        logger.info("记忆新增：type=%s content=%r", fact["type"], fact["content"][:40])
        count += 1

    return count
