from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.logging_config import get_logger
from app.memory.schemas import (
    WorldbookCreateRequest,
    WorldbookListResponse,
    WorldbookResponse,
    WorldbookUpdateRequest,
)
from app.memory.worldbook import (
    count_entries,
    delete_entry,
    get_entry,
    list_entries,
    store_entry,
    update_entry,
)

logger = get_logger("api.worldbook")

router = APIRouter(prefix="/worldbook", tags=["worldbook"])


def _require_enabled(request: Request):
    settings = request.app.state.settings
    if not settings.features.rag:
        raise HTTPException(
            status_code=503,
            detail="RAG 模块未开启（features.rag=false）",
        )
    return settings


def _entry_to_response(entry) -> WorldbookResponse:
    return WorldbookResponse(
        id=entry.id,
        title=entry.title,
        keywords=entry.keywords,
        content=entry.content,
        importance=entry.importance,
        status=entry.status,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.get("", response_model=WorldbookListResponse)
async def list_entries_endpoint(
    request: Request,
    status: str | None = Query(default="active"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    settings = _require_enabled(request)

    # 不带 status 仍默认只看活跃条目（前端依赖这个默认值）；
    # 显式传空串（?status=）表示不过滤——别让它变成 `WHERE status = ''`
    status = status or None

    entries = await list_entries(
        settings, status=status, limit=limit, offset=offset
    )
    return WorldbookListResponse(
        total=await count_entries(settings, status=status),
        items=[_entry_to_response(e) for e in entries],
    )


@router.get("/{entry_id}", response_model=WorldbookResponse)
async def get_entry_endpoint(
    request: Request,
    entry_id: str
):
    settings = _require_enabled(request)
    entry = await get_entry(settings, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="条目不存在")
    return _entry_to_response(entry)


@router.post("", response_model=WorldbookResponse)
async def create_entry_endpoint(
    request: Request,
    body: WorldbookCreateRequest,
):
    settings = _require_enabled(request)
    entry = await store_entry(
        settings,
        title=body.title,
        content=body.content,
        keywords=body.keywords,
        importance=body.importance,
    )
    logger.info("worldbook 条目已创建：id=%s title=%r", entry.id, entry.title)
    return _entry_to_response(entry)


@router.patch("/{entry_id}", response_model=WorldbookResponse)
async def update_entry_endpoint(
    request: Request,
    entry_id: str,
    body: WorldbookUpdateRequest,
):
    settings = _require_enabled(request)

    if (
        body.title is None
        and body.content is None
        and body.keywords is None
        and body.importance is None
        and body.status is None
    ):
        raise HTTPException(status_code=400, detail="至少提供一个可更新字段")

    updated = await update_entry(
        settings,
        entry_id,
        title=body.title,
        content=body.content,
        keywords=body.keywords,
        importance=body.importance,
        status=body.status,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="条目不存在")

    logger.info("worldbook 条目已更新：id=%s", entry_id)
    return _entry_to_response(updated)


@router.delete("/{entry_id}")
async def delete_entry_endpoint(
    request: Request,
    entry_id: str,
    hard: bool = Query(default=False),
):
    settings = _require_enabled(request)
    ok = await delete_entry(settings, entry_id, hard=hard)
    if not ok:
        raise HTTPException(status_code=404, detail="条目不存在")
    logger.info("worldbook 条目已删除：id=%s hard=%s", entry_id, hard)
    return {"ok": True, "id": entry_id, "hard": hard}
