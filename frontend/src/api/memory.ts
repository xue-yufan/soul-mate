import { http } from "./http";
import type {
    AuditListResponse,
    MemoryFact,
    MemoryFactListResponse,
    MemoryFactUpdatePayload,
} from "@/types/knowledge";

export interface ListMemoryOptions {
    status?: string | null;
    type?: string | null;
    limit?: number;
    offset?: number;
}

export interface ListAuditOptions {
    targetId?: string | null;
    limit?: number;
    offset?: number;
}

export const memoryApi = {
    list(options: ListMemoryOptions = {}): Promise<MemoryFactListResponse> {
        const params = new URLSearchParams();
        // 不带 status 表示不过滤（后端 Query(default=None)）；"全部"那个选项
        // 就是靠这里不发参数表达的。前端传空串也有后端兜底（会归一成 None）
        if (options.status) {
            params.set("status", options.status);
        }
        if (options.type) {
            params.set("type", options.type);
        }
        if (options.limit !== undefined) {
            params.set("limit", String(options.limit));
        }
        if (options.offset !== undefined) {
            params.set("offset", String(options.offset));
        }
        const qs = params.toString();
        return http.get<MemoryFactListResponse>(`/memory/facts${qs ? `?${qs}` : ""}`);
    },

    get(id: string): Promise<MemoryFact> {
        return http.get<MemoryFact>(`/memory/facts/${id}`);
    },

    update(id: string, payload: MemoryFactUpdatePayload): Promise<MemoryFact> {
        return http.patch<MemoryFact>(`/memory/facts/${id}`, payload);
    },

    remove(id: string, hard = false): Promise<{ ok: boolean }> {
        const qs = hard ? "?hard=true" : "";
        return http.delete<{ ok: boolean }>(`/memory/facts/${id}${qs}`);
    },

    restore(id: string): Promise<MemoryFact> {
        return http.post<MemoryFact>(`/memory/facts/${id}/restore`);
    },

    audit(options: ListAuditOptions = {}): Promise<AuditListResponse> {
        const params = new URLSearchParams();
        if (options.targetId) {
            params.set("target_id", options.targetId);
        }
        if (options.limit !== undefined) {
            params.set("limit", String(options.limit));
        }
        if (options.offset !== undefined) {
            params.set("offset", String(options.offset));
        }
        const qs = params.toString();
        return http.get<AuditListResponse>(`/memory/audit${qs ? `?${qs}` : ""}`);
    },
};
