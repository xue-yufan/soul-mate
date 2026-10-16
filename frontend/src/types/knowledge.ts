// ---------- 记忆 ----------

export interface MemoryFact {
    id: string;
    type: string;
    content: string;
    importance: number;
    source: string | null;
    status: string;
    created_at: string;
    updated_at: string;
}

export interface MemoryFactListResponse {
    total: number;
    items: MemoryFact[];
}

export interface MemoryFactUpdatePayload {
    content?: string;
    importance?: number;
    status?: "active" | "archived" | "deleted";
}

// ---------- 文档 ----------

export interface DocumentItem {
    id: string;
    title: string;
    source: string | null;
    mime_type: string | null;
    size_bytes: number;
    chunk_count: number;
    status: string;
    created_at: string;
    updated_at: string;
}

export interface DocumentListResponse {
    total: number;
    items: DocumentItem[];
}

export interface DocumentChunk {
    id: string;
    index: number;
    content: string;
}

export interface DocumentImportPayload {
    title: string;
    content: string;
    source?: string;
    mime_type?: string;
}

// ---------- worldbook ----------

export interface WorldbookEntry {
    id: string;
    title: string;
    keywords: string[];
    content: string;
    importance: number;
    status: string;
    created_at: string;
    updated_at: string;
}

export interface WorldbookListResponse {
    total: number;
    items: WorldbookEntry[];
}

export interface WorldbookCreatePayload {
    title: string;
    content: string;
    keywords: string[];
    importance: number;
}

export interface WorldbookUpdatePayload {
    title?: string;
    content?: string;
    keywords?: string[];
    importance?: number;
    status?: "active" | "archived" | "deleted";
}

// ---------- 通用 ----------

export interface ApiError {
    status: number;
    detail: string;
}

// ---------- 审计 ----------

export type AuditAction = "update" | "delete_soft" | "delete_hard" | "restore";

export interface AuditEntry {
    id: number;
    action: string;
    target_id: string;
    actor: string;
    before: Record<string, unknown> | null;
    after: Record<string, unknown> | null;
    created_at: string;
}

export interface AuditListResponse {
    total: number;
    items: AuditEntry[];
}
