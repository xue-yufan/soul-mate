import { http } from "./http";
import type {
    DocumentChunk,
    DocumentImportPayload,
    DocumentItem,
    DocumentListResponse,
} from "@/types/knowledge";

export interface ListDocumentsOptions {
    status?: string | null;
    limit?: number;
    offset?: number;
}

export interface DocumentUpdatePayload {
    title?: string;
    status?: "active" | "archived" | "deleted";
}

export const documentsApi = {
    list(options: ListDocumentsOptions = {}): Promise<DocumentListResponse> {
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
        return http.get<DocumentListResponse>(`/documents${qs ? `?${qs}` : ""}`);
    },

    get(id: string): Promise<DocumentItem> {
        return http.get<DocumentItem>(`/documents/${id}`);
    },

    chunks(id: string): Promise<DocumentChunk[]> {
        return http.get<DocumentChunk[]>(`/documents/${id}/chunks`);
    },

    importText(payload: DocumentImportPayload): Promise<DocumentItem> {
        return http.post<DocumentItem>("/documents", payload);
    },

    /**
     * 从服务端本地文件导入。
     *
     * path 由主进程的 openFileDialog() 返回，是绝对路径。
     * 必须 encodeURIComponent，Windows 路径含反斜杠、空格、中文。
     */
    importFile(path: string): Promise<DocumentItem> {
        const qs = new URLSearchParams({ path }).toString();
        return http.post<DocumentItem>(`/documents/import_file?${qs}`);
    },

    update(id: string, payload: DocumentUpdatePayload): Promise<DocumentItem> {
        return http.patch<DocumentItem>(`/documents/${id}`, payload);
    },

    remove(id: string, hard = false): Promise<{ ok: boolean }> {
        const qs = hard ? "?hard=true" : "";
        return http.delete<{ ok: boolean }>(`/documents/${id}${qs}`);
    },
};
