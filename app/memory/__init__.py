"""记忆模块"""

from __future__ import annotations

from app.memory.audit import count_audit, list_audit, log_change
from app.memory.documents import (
    Document,
    count_documents,
    delete_document,
    get_chunks,
    get_document,
    import_file,
    import_text,
    list_documents,
)
from app.memory.embedder import (
    Embedder,
    get_embedder,
    reset_embedder,
)
from app.memory.extract import extract_and_store, extract_facts
from app.memory.retrieve import (
    RecalledFact,
    RecallItem,
    format_for_prompt,
    format_items_for_prompt,
    recall,
    recall_all,
    recall_all_and_format,
    recall_and_format,
    recall_documents,
    recall_worldbook,
    time_decay,
)
from app.memory.schemas import (
    AuditEntry,
    AuditListResponse,
    ChunkResponse,
    DocumentImportRequest,
    DocumentListResponse,
    DocumentResponse,
    FactListResponse,
    FactResponse,
    FactUpdateRequest,
    WorldbookCreateRequest,
    WorldbookListResponse,
    WorldbookResponse,
    WorldbookUpdateRequest,
)
from app.memory.store import (
    MemoryFact,
    count_facts,
    delete_fact,
    get_chroma_client,
    get_collection,
    get_fact,
    get_facts_by_ids,
    list_facts,
    rebuild_index,
    reset_chroma_client,
    search_similar,
    store_fact,
    update_fact,
)
from app.memory.worldbook import (
    RetrievedEntry,
    WorldbookEntry,
    count_entries,
    delete_entry,
    get_entry,
    list_entries,
    retrieve_entries,
    retrieve_entries_and_format,
    store_entry,
    update_entry,
)

__all__ = [
    # embedder
    "Embedder",
    "get_embedder",
    "reset_embedder",
    # store
    "MemoryFact",
    "store_fact",
    "update_fact",
    "delete_fact",
    "get_fact",
    "list_facts",
    "get_facts_by_ids",
    "count_facts",
    "search_similar",
    "rebuild_index",
    "get_chroma_client",
    "get_collection",
    "reset_chroma_client",
    # extract
    "extract_facts",
    "extract_and_store",
    # retrieve
    "RecalledFact",
    "RecallItem",
    "recall",
    "recall_and_format",
    "recall_all",
    "recall_all_and_format",
    "recall_documents",
    "recall_worldbook",
    "format_for_prompt",
    "format_items_for_prompt",
    "time_decay",
    # audit
    "log_change",
    "list_audit",
    "count_audit",
    # schemas
    "FactResponse",
    "FactListResponse",
    "FactUpdateRequest",
    "AuditEntry",
    "AuditListResponse",
        # documents
    "Document",
    "import_text",
    "import_file",
    "get_document",
    "get_chunks",
    "count_documents",
    "list_documents",
    "delete_document",
    # worldbook
    "WorldbookEntry",
    "RetrievedEntry",
    "store_entry",
    "update_entry",
    "delete_entry",
    "get_entry",
    "list_entries",
    "count_entries",
    "retrieve_entries",
    "retrieve_entries_and_format",
    # schemas
    "DocumentResponse",
    "DocumentListResponse",
    "DocumentImportRequest",
    "ChunkResponse",
    "WorldbookResponse",
    "WorldbookListResponse",
    "WorldbookCreateRequest",
    "WorldbookUpdateRequest",
]
