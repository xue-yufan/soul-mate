from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.config import Settings
from app.logging_config import get_logger
from app.memory.embedder import Embedder, get_embedder
from app.memory.store import (
    MemoryFact,
    get_facts_by_ids,
    search_similar,
)

logger = get_logger("memory.retrieve")


@dataclass
class RecalledFact:
    """带打分的召回结果"""

    fact: MemoryFact
    # 1 - distance，越大越相似
    similarity: float
    # 0~1，越新越大
    decay: float
    # 综合分
    score: float


def _parse_iso(ts: str) -> datetime:
    """解析 ISO8601 字符串，失败时返回 now（衰减接近 0.5）"""

    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def time_decay(created_at: str, half_life_days: int) -> float:
    """
    指数时间衰减，半衰期越长，旧记忆衰减越慢

    age=0 天      → 1.0
    age=half_life → 0.5
    age=2*half    → 0.25
    """

    if half_life_days <= 0:
        return 1.0

    now = datetime.now(timezone.utc)
    created = _parse_iso(created_at)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)

    age_days = max(0.0, (now - created).total_seconds() / 86400.0)
    return 0.5 ** (age_days / half_life_days)


def _score(
    similarity: float,
    decay: float,
    importance: float,
    *,
    similarity_weight: float = 0.6,
    decay_weight: float = 0.2,
    importance_weight: float = 0.2,
) -> float:
    """
    综合打分。

    权重默认 0.6 / 0.2 / 0.2：
    - 语义相似度主导
    - 时间衰减和重要性作为调节

    similarity 和 decay 都归一化到 0~1，importance 本身在 0~1
    最终 score 落在 0~1
    """

    return (
        similarity_weight * similarity
        + decay_weight * decay
        + importance_weight * importance
    )


async def recall(
    settings: Settings,
    query: str,
    *,
    top_k: int | None = None,
    embedder: Embedder | None = None,
) -> list[RecalledFact]:
    """
    召回相关记忆

    top_k 默认从 settings.memory.top_k 读
    候选先取 top_k * 2，重排后取前 top_k
    """

    if not query or not query.strip():
        logger.info("记忆召回跳过：query 为空")
        return []

    # features.memory 关闭时不召回记忆（调用方可能因为 rag 开启而走到这里）
    if not settings.features.memory:
        logger.info("记忆召回跳过：features.memory=false")
        return []

    top_k = top_k or settings.memory.top_k
    if top_k <= 0:
        return []

    min_importance = settings.memory.min_importance
    half_life = settings.memory.time_decay_days

    emb = embedder or get_embedder(settings)

    # 1. 向量检索候选
    candidates = await search_similar(
        settings, query, top_k=top_k * 2, embedder=emb
    )
    if not candidates:
        return []

    # 2. 批量取完整记录
    fact_ids = [fid for fid, _ in candidates]
    facts = await get_facts_by_ids(settings, fact_ids)
    by_id = {f.id: f for f in facts}

    # 3. 重排
    recalled: list[RecalledFact] = []
    for fact_id, distance in candidates:
        fact = by_id.get(fact_id)
        if fact is None:
            continue
        if fact.importance < min_importance:
            continue

        similarity = max(0.0, 1.0 - distance)
        decay = time_decay(fact.created_at, half_life)
        score = _score(similarity, decay, fact.importance)

        recalled.append(
            RecalledFact(
                fact=fact,
                similarity=similarity,
                decay=decay,
                score=score,
            )
        )

    # 4. 排序：score 降序 → importance 降序 → created_at 降序
    recalled.sort(
        key=lambda r: (r.score, r.fact.importance, r.fact.created_at),
        reverse=True,
    )

    top = recalled[:top_k]

    if top:
        logger.debug("召回 %d 条：query=%r top_score=%.3f", len(top), query[:40], top[0].score)
    return top


def format_for_prompt(
    recalled: list[RecalledFact],
    *,
    header: str = "【相关记忆】",
) -> str:
    """
    格式化成可注入 system prompt 的文本

    形如：
        【相关记忆】
        - 用户喜欢喝美式
        - 用户的名字是小明
        - 用户最近在学 Rust

    空列表返回空串
    """

    if not recalled:
        return ""

    lines = [header]
    for r in recalled:
        lines.append(f"- {r.fact.content}")
    return "\n".join(lines)


async def recall_and_format(
    settings: Settings,
    query: str,
    *,
    top_k: int | None = None,
    embedder: Embedder | None = None,
    header: str = "【相关记忆】",
) -> str:
    """召回并格式化。空结果返回空串"""

    recalled = await recall(
        settings,
        query,
        top_k=top_k,
        embedder=embedder
    )
    return format_for_prompt(recalled, header=header)


SourceKind = Literal["memory", "document", "worldbook"]


@dataclass
class RecallItem:
    """合并后的统一召回项。"""

    kind: SourceKind
    title: str              # 记忆为空串，文档为文档标题，worldbook 为条目名
    content: str
    score: float            # 归一化后的综合分，0~1
    raw_score: float        # 来源内的原始分，供调试
    meta: dict


async def recall_documents(
    settings: Settings,
    query: str,
    *,
    top_k: int | None = None,
    embedder: Embedder | None = None,
) -> list[RecallItem]:
    """召回文档片段，包装成 RecallItem"""

    if not settings.features.rag:
        return []

    from app.memory.rag import retrieve_documents

    chunks = await retrieve_documents(
        settings,
        query,
        top_k=top_k,
        embedder=embedder
    )
    return [
        RecallItem(
            kind="document",
            title=c.title,
            content=c.content,
            score=c.similarity,
            raw_score=c.similarity,
            meta={
                "document_id": c.document_id,
                "chunk_index": c.chunk_index,
                "source": c.source,
            },
        )
        for c in chunks
    ]


async def recall_worldbook(
    settings: Settings,
    query: str,
    *,
    top_k: int | None = None,
    embedder: Embedder | None = None,
) -> list[RecallItem]:
    """召回设定条目，包装成 RecallItem"""

    if not settings.features.rag:
        return []

    from app.memory.worldbook import retrieve_entries

    entries = await retrieve_entries(
        settings,
        query,
        top_k=top_k,
        embedder=embedder,
    )
    return [
        RecallItem(
            kind="worldbook",
            title=e.entry.title,
            content=e.entry.content,
            score=e.score,
            raw_score=e.score,
            meta={
                "entry_id": e.entry.id,
                "keyword_hit": e.keyword_hit,
            },
        )
        for e in entries
    ]


_SOURCE_WEIGHTS: dict[SourceKind, float] = {
    "memory": 1.0,
    "worldbook": 0.9,
    "document": 0.8,
}


def _normalize_scores(items: list[RecallItem]) -> None:
    """对一路结果做 min-max 归一，单条时设为 1.0"""

    if not items:
        return
    scores = [i.score for i in items]
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-9:
        for i in items:
            i.score = 1.0
        return
    for i in items:
        i.score = (i.score - lo) / (hi - lo)


async def recall_all(
    settings: Settings,
    query: str,
    *,
    total_top_k: int | None = None,
    embedder: Embedder | None = None,
) -> list[RecallItem]:
    """
    合并三路召回

    步骤：
    1. 各来源独立召回，失败不影响其他
    2. 每路内部 min-max 归一
    3. 乘来源权重
    4. 合并排序，取 total_top_k
    """

    if not query or not query.strip():
        logger.info("召回跳过：query 为空")
        return []

    # 两个开关都关，直接返回，不加载嵌入模型
    if not settings.features.memory and not settings.features.rag:
        logger.info("召回跳过：features.memory 与 features.rag 均为 false")
        return []

    # 上限按已开启的来源推导，不用 settings.memory.top_k 兜底：
    # rag-only 时不该被记忆配置卡住，memory.top_k=0 也不该连同文档一起丢掉
    if total_top_k is None:
        total_top_k = 0
        if settings.features.memory:
            total_top_k += settings.memory.top_k
        if settings.features.rag:
            total_top_k += settings.rag.top_k + settings.rag.worldbook_top_k
    if total_top_k <= 0:
        return []

    emb = embedder or get_embedder(settings)

    # 嵌入模型不可用（预热失败等）时直接跳过，别让每句话都卡在加载重试上
    unavailable = getattr(emb, "unavailable_reason", None)
    if unavailable:
        logger.warning("召回跳过：嵌入模型不可用（%s）", unavailable)
        return []

    items: list[RecallItem] = []

    # 1. 记忆
    try:
        facts = await recall(
            settings,
            query,
            embedder=emb
        )
        for f in facts:
            items.append(
                RecallItem(
                    kind="memory",
                    title="",
                    content=f.fact.content,
                    score=f.score,
                    raw_score=f.score,
                    meta={
                        "fact_id": f.fact.id,
                        "type": f.fact.type,
                        "importance": f.fact.importance,
                    },
                )
            )
    except Exception:
        logger.exception("记忆召回失败")

    # 2. 文档
    try:
        items.extend(await recall_documents(
            settings,
            query,
            embedder=emb)
        )
    except Exception:
        logger.exception("文档召回失败")

    # 3. worldbook
    try:
        items.extend(await recall_worldbook(
            settings,
            query,
            embedder=emb)
        )
    except Exception:
        logger.exception("worldbook 召回失败")

    if not items:
        logger.info(
            "召回为空：query=%r memory=%s rag=%s",
            query[:40], settings.features.memory, settings.features.rag,
        )
        return []

    # 分组归一
    grouped: dict[SourceKind, list[RecallItem]] = {}
    for it in items:
        grouped.setdefault(it.kind, []).append(it)

    for kind, group in grouped.items():
        _normalize_scores(group)
        weight = _SOURCE_WEIGHTS.get(kind, 1.0)
        for it in group:
            it.score = it.score * weight

    # 合并排序
    items.sort(key=lambda i: i.score, reverse=True)
    top = items[:total_top_k]

    if top:
        kinds = [i.kind for i in top]
        logger.debug("合并召回 %d 条：query=%r kinds=%s top_score=%.3f", len(top), query[:40], kinds, top[0].score)
    return top


def format_items_for_prompt(items: list[RecallItem]) -> str:
    """
    按来源分组格式化：分组顺序固定：记忆 → 设定 → 文档
    """

    if not items:
        return ""

    headers = {
        "memory": "【相关记忆】",
        "worldbook": "【设定】",
        "document": "【相关文档】",
    }

    grouped: dict[SourceKind, list[RecallItem]] = {}
    for it in items:
        grouped.setdefault(it.kind, []).append(it)

    parts: list[str] = []
    for kind in ("memory", "worldbook", "document"):
        group = grouped.get(kind)
        if not group:
            continue
        lines = [headers[kind]]
        for it in group:
            if kind == "document" and it.title:
                lines.append(f"- [{it.title}] {it.content}")
            elif kind == "worldbook" and it.title:
                lines.append(f"- {it.title}：{it.content}")
            else:
                lines.append(f"- {it.content}")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


async def recall_all_and_format(
    settings: Settings,
    query: str,
    *,
    total_top_k: int | None = None,
    embedder: Embedder | None = None,
) -> str:
    """召回并格式化：空结果返回空串"""

    items = await recall_all(
        settings,
        query,
        total_top_k=total_top_k,
        embedder=embedder
    )
    return format_items_for_prompt(items)
