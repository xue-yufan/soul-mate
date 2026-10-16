from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.logging_config import get_logger
from app.memory.audit import count_audit, list_audit, log_change
from app.memory.schemas import (
    AuditEntry,
    AuditListResponse,
    FactListResponse,
    FactResponse,
    FactUpdateRequest,
)
from app.memory.store import (
    count_facts,
    delete_fact,
    get_fact,
    list_facts,
    update_fact,
)

logger = get_logger("api.memory")

router = APIRouter(prefix="/memory", tags=["memory"])


def _require_enabled(request: Request):
    """记忆模块未开启时返回 503"""

    settings = request.app.state.settings
    if not settings.features.memory:
        raise HTTPException(status_code=503, detail="记忆模块未开启（features.memory=false）")
    return settings


def _fact_to_response(fact) -> FactResponse:
    return FactResponse(
        id=fact.id,
        type=fact.type,
        content=fact.content,
        importance=fact.importance,
        source=fact.source,
        status=fact.status,
        created_at=fact.created_at,
        updated_at=fact.updated_at,
    )


@router.get("/facts", response_model=FactListResponse)
async def list_facts_endpoint(
    request: Request,
    status: str | None = Query(default=None),
    type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """列出记忆，不带 status / type 表示不过滤

    默认值必须是 None 而不是 "active"：前端的「全部」选项是靠**不发**这个参数
    表达的，写成 "active" 会让它和「活跃」完全同义。空串（手写的 ?status=）
    也归一成 None——否则会变成 `WHERE status = ''`，一条都查不到。
    """

    settings = _require_enabled(request)

    # 归一化一次，list 和 count 都吃它：两边条件不一致时会出现
    # 「列表里有 N 条、total 却是 0」
    status = status or None
    type = type or None

    facts = await list_facts(
        settings,
        status=status,
        type=type,
        limit=limit,
        offset=offset,
    )
    return FactListResponse(
        total=await count_facts(settings, status=status, type=type),
        items=[_fact_to_response(f) for f in facts],
    )


@router.get("/facts/{fact_id}", response_model=FactResponse)
async def get_fact_endpoint(
    request: Request,
    fact_id: str
):
    """单条"""

    settings = _require_enabled(request)
    fact = await get_fact(settings, fact_id)
    if fact is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return _fact_to_response(fact)


@router.patch("/facts/{fact_id}", response_model=FactResponse)
async def update_fact_endpoint(
    request: Request,
    fact_id: str,
    body: FactUpdateRequest,
):
    """编辑"""

    settings = _require_enabled(request)

    before = await get_fact(settings, fact_id)
    if before is None:
        raise HTTPException(status_code=404, detail="记忆不存在")

    if (
        body.content is None
        and body.importance is None
        and body.status is None
    ):
        raise HTTPException(status_code=400, detail="至少提供一个可更新字段")

    updated = await update_fact(
        settings,
        fact_id,
        content=body.content,
        importance=body.importance,
        status=body.status,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="记忆不存在")

    await log_change(
        settings,
        action="update",
        fact_id=fact_id,
        before={
            "content": before.content,
            "importance": before.importance,
            "status": before.status,
        },
        after={
            "content": updated.content,
            "importance": updated.importance,
            "status": updated.status,
        },
    )

    logger.info("记忆已编辑：id=%s", fact_id)
    return _fact_to_response(updated)


@router.delete("/facts/{fact_id}")
async def delete_fact_endpoint(
    request: Request,
    fact_id: str,
    hard: bool = Query(default=False),
):
    """删除记忆：默认软删，hard=true 物理删"""

    settings = _require_enabled(request)

    before = await get_fact(settings, fact_id)
    if before is None:
        raise HTTPException(status_code=404, detail="记忆不存在")

    ok = await delete_fact(settings, fact_id, hard=hard)
    if not ok:
        raise HTTPException(status_code=404, detail="记忆不存在")

    await log_change(
        settings,
        action="delete_hard" if hard else "delete_soft",
        fact_id=fact_id,
        before={
            "content": before.content,
            "importance": before.importance,
            "status": before.status,
        },
    )

    logger.info("记忆已删除：id=%s hard=%s", fact_id, hard)
    return {"ok": True, "id": fact_id, "hard": hard}


@router.post("/facts/{fact_id}/restore", response_model=FactResponse)
async def restore_fact_endpoint(
    request: Request,
    fact_id: str
):
    """把软删的记忆恢复成 active"""

    settings = _require_enabled(request)

    before = await get_fact(settings, fact_id)
    if before is None:
        raise HTTPException(status_code=404, detail="记忆不存在")
    if before.status != "deleted":
        raise HTTPException(status_code=400, detail=f"只有 deleted 状态可恢复，当前：{before.status}")

    restored = await update_fact(settings, fact_id, status="active")
    if restored is None:
        raise HTTPException(status_code=404, detail="记忆不存在")

    await log_change(
        settings,
        action="restore",
        fact_id=fact_id,
        before={"status": before.status},
        after={"status": restored.status},
    )

    logger.info("记忆已恢复：id=%s", fact_id)
    return _fact_to_response(restored)


@router.get("/audit", response_model=AuditListResponse)
async def list_audit_endpoint(
    request: Request,
    event_type: str | None = Query(
        default="memory_fact",
        description="事件类型；传 all 表示不过滤（记忆与工具调用都返回）",
    ),
    target_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    settings = _require_enabled(request)

    # all → 不过滤；其余按给定类型查
    wanted = None if event_type == "all" else event_type

    entries = await list_audit(
        settings,
        event_type=wanted,
        target_id=target_id,
        limit=limit,
        offset=offset,
    )
    return AuditListResponse(
        total=await count_audit(
            settings, event_type=wanted, target_id=target_id
        ),
        items=[AuditEntry(**e) for e in entries],
    )
