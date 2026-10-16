/**
 * 历史消息 API
 *
 * 对应 backend/app/api/history.py
 */

import { http } from "./http";

export interface HistoryToolCall {
    id: string;
    name: string;
    args: Record<string, unknown>;
}

export interface HistoryMessage {
    id: string;
    role: "user" | "assistant" | "tool";
    content: string;
    created_at: string;
    tokens_in: number;
    tokens_out: number;
    model: string;
    /** 中间那条 assistant 带工具调用请求；只有它会有值 */
    tool_calls?: HistoryToolCall[];
    /** 仅 role=tool：对应哪次工具调用 */
    tool_call_id?: string | null;
    name?: string | null;
    /** 仅 role=tool：结果状态 ok / error / denied */
    status?: string | null;
}

export interface HistoryResponse {
    total: number;
    items: HistoryMessage[];
}

export interface ClearHistoryResponse {
    ok: boolean;
    deleted: number;
}

export interface ListHistoryOptions {
    limit?: number;
    offset?: number;
}

export const historyApi = {
    list(options: ListHistoryOptions = {}): Promise<HistoryResponse> {
        const params = new URLSearchParams();
        if (options.limit !== undefined) {
            params.set("limit", String(options.limit));
        }
        if (options.offset !== undefined) {
            params.set("offset", String(options.offset));
        }
        const qs = params.toString();
        return http.get<HistoryResponse>(`/history/messages${qs ? `?${qs}` : ""}`);
    },

    clear(): Promise<ClearHistoryResponse> {
        return http.delete<ClearHistoryResponse>("/history/messages");
    },
};
