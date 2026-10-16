from __future__ import annotations

from pydantic import BaseModel, Field


class FactResponse(BaseModel):
    """单条记忆的响应"""

    id: str
    type: str
    content: str
    importance: float
    source: str | None
    status: str
    created_at: str
    updated_at: str


class FactListResponse(BaseModel):
    """记忆列表响应"""

    total: int
    items: list[FactResponse]


class FactUpdateRequest(BaseModel):
    """更新记忆的请求体，所有字段可选"""

    content: str | None = Field(default=None, min_length=1, max_length=2000)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    status: str | None = Field(default=None, pattern="^(active|archived|deleted)$")


class AuditEntry(BaseModel):
    """审计记录"""

    id: int
    action: str
    target_id: str
    actor: str
    before: dict | None
    after: dict | None
    created_at: str


class AuditListResponse(BaseModel):
    """审计列表响应"""

    total: int
    items: list[AuditEntry]


class DocumentResponse(BaseModel):
    id: str
    title: str
    source: str | None
    mime_type: str | None
    size_bytes: int
    chunk_count: int
    status: str
    created_at: str
    updated_at: str


class DocumentListResponse(BaseModel):
    total: int
    items: list[DocumentResponse]


class DocumentImportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    source: str | None = None
    mime_type: str = "text/plain"


class DocumentUpdateRequest(BaseModel):
    """更新文档元数据的请求体，所有字段可选

    只有 active / deleted 两种取值：documents 表本来就只写这两个
    （worldbook / memory 才有 archived），放进来会变成界面上看不见的状态。
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: str | None = Field(default=None, pattern="^(active|deleted)$")


class ChunkResponse(BaseModel):
    id: str
    index: int
    content: str


class WorldbookResponse(BaseModel):
    id: str
    title: str
    keywords: list[str]
    content: str
    importance: float
    status: str
    created_at: str
    updated_at: str


class WorldbookListResponse(BaseModel):
    total: int
    items: list[WorldbookResponse]


class WorldbookCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)
    importance: float = Field(default=0.7, ge=0.0, le=1.0)


class WorldbookUpdateRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1)
    keywords: list[str] | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    status: str | None = Field(default=None, pattern="^(active|archived|deleted)$")
