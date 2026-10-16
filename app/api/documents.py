from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from app.logging_config import get_logger
from app.memory.documents import (
    DocumentConflictError,
    DocumentReindexError,
    count_documents,
    delete_document,
    get_chunks,
    get_document,
    import_file,
    import_text,
    list_documents,
    update_document,
)
from app.memory.schemas import (
    ChunkResponse,
    DocumentImportRequest,
    DocumentListResponse,
    DocumentResponse,
    DocumentUpdateRequest,
)

logger = get_logger("api.documents")

router = APIRouter(prefix="/documents", tags=["documents"])


def _require_enabled(request: Request):
    settings = request.app.state.settings
    if not settings.features.rag:
        raise HTTPException(
            status_code=503,
            detail="RAG 模块未开启（features.rag=false）",
        )
    return settings


def _doc_to_response(doc) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        source=doc.source,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        chunk_count=doc.chunk_count,
        status=doc.status,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents_endpoint(
    request: Request,
    status: str | None = Query(default="active"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    settings = _require_enabled(request)

    # 不带 status 仍默认只看活跃文档（前端依赖这个默认值）；
    # 显式传空串（?status=）表示不过滤——别让它变成 `WHERE status = ''`
    status = status or None

    docs = await list_documents(
        settings, status=status, limit=limit, offset=offset
    )
    return DocumentListResponse(
        total=await count_documents(settings, status=status),
        items=[_doc_to_response(d) for d in docs],
    )


@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document_endpoint(request: Request, doc_id: str):
    settings = _require_enabled(request)
    doc = await get_document(settings, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return _doc_to_response(doc)


@router.get("/{doc_id}/chunks", response_model=list[ChunkResponse])
async def get_chunks_endpoint(request: Request, doc_id: str):
    settings = _require_enabled(request)
    doc = await get_document(settings, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    chunks = await get_chunks(settings, doc_id)
    return [ChunkResponse(**c) for c in chunks]


@router.post("", response_model=DocumentResponse)
async def import_text_endpoint(
    request: Request,
    body: DocumentImportRequest,
):
    settings = _require_enabled(request)
    doc = await import_text(
        settings,
        title=body.title,
        content=body.content,
        source=body.source,
        mime_type=body.mime_type,
    )
    logger.info("文档已导入：id=%s title=%r", doc.id, doc.title)
    return _doc_to_response(doc)


@router.post("/import_file", response_model=DocumentResponse)
async def import_file_endpoint(
    request: Request,
    path: str = Query(..., description="服务端文件绝对路径"),
    title: str | None = Query(default=None),
):
    settings = _require_enabled(request)
    try:
        doc = await import_file(settings, path, title=title)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    logger.info("文件已导入：id=%s title=%r", doc.id, doc.title)
    return _doc_to_response(doc)


@router.patch("/{doc_id}", response_model=DocumentResponse)
async def update_document_endpoint(
    request: Request,
    doc_id: str,
    body: DocumentUpdateRequest,
):
    """
    更新文档标题或状态

    status='active' 可以恢复软删的文档，会重建向量；嵌入模型不可用时返回 503。
    和同内容的活跃文档冲突时返回 409。
    """

    settings = _require_enabled(request)

    if body.title is None and body.status is None:
        raise HTTPException(status_code=400, detail="至少提供一个可更新字段")

    try:
        updated = await update_document(
            settings,
            doc_id,
            title=body.title,
            status=body.status,
        )
    except DocumentConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DocumentReindexError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if updated is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    logger.info("文档已更新：id=%s", doc_id)
    return _doc_to_response(updated)


@router.delete("/{doc_id}")
async def delete_document_endpoint(
    request: Request,
    doc_id: str,
    hard: bool = Query(default=False),
):
    settings = _require_enabled(request)
    ok = await delete_document(settings, doc_id, hard=hard)
    if not ok:
        raise HTTPException(status_code=404, detail="文档不存在")
    logger.info("文档已删除：id=%s hard=%s", doc_id, hard)
    return {"ok": True, "id": doc_id, "hard": hard}
