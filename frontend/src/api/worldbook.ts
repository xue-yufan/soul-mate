import { http } from "./http";
import type {
    WorldbookCreatePayload,
    WorldbookEntry,
    WorldbookListResponse,
    WorldbookUpdatePayload,
} from "@/types/knowledge";

export interface ListWorldbookOptions {
    status?: string | null;
    limit?: number;
    offset?: number;
}

export const worldbookApi = {
    list(options: ListWorldbookOptions = {}): Promise<WorldbookListResponse> {
        const params = new URLSearchParams();
        // 传 null 会拼出 `?status=`（空串），后端当「不过滤」处理；
        // 完全不带这个参数则走后端默认值（只看 active）
        if (options.status !== undefined) {
            params.set("status", options.status ?? "");
        }
        if (options.limit !== undefined) {
            params.set("limit", String(options.limit));
        }
        if (options.offset !== undefined) {
            params.set("offset", String(options.offset));
        }
        const qs = params.toString();
        return http.get<WorldbookListResponse>(`/worldbook${qs ? `?${qs}` : ""}`);
    },

    get(id: string): Promise<WorldbookEntry> {
        return http.get<WorldbookEntry>(`/worldbook/${id}`);
    },

    create(payload: WorldbookCreatePayload): Promise<WorldbookEntry> {
        return http.post<WorldbookEntry>("/worldbook", payload);
    },

    update(id: string, payload: WorldbookUpdatePayload): Promise<WorldbookEntry> {
        return http.patch<WorldbookEntry>(`/worldbook/${id}`, payload);
    },

    remove(id: string, hard = false): Promise<{ ok: boolean }> {
        const qs = hard ? "?hard=true" : "";
        return http.delete<{ ok: boolean }>(`/worldbook/${id}${qs}`);
    },
};
