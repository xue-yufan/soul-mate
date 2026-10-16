import { ref, watch, type Ref } from "vue";

import { historyApi } from "@/api/history";
import type { UseTtsResult } from "./useTts";
import type { UseWebSocketResult } from "./useWebSocket";
import type { Emotion, ErrorData, Expert, ServerEvent } from "@/types/protocol";
import { ERROR_MESSAGES } from "@/types/protocol";

export type MessageRole = "user" | "assistant" | "error" | "tool";

export type ToolStatus = "running" | "confirm" | "ok" | "error" | "denied";

export interface ToolInfo {
    toolCallId: string;
    name: string;
    args: Record<string, unknown>;
    status: ToolStatus;
    content?: string;
    error?: string;
    meta?: Record<string, unknown>;
    confirmId?: string;
    /** 她开口问的那句话（后端按人格模板渲染） */
    prompt?: string;
    /** 确认截止时间（epoch ms），用于卡片上的倒计时 */
    confirmDeadline?: number;
}

export interface ChatMessage {
    id: string;
    role: MessageRole;
    content: string;
    streaming: boolean;
    createdAt: number;
    stats?: {
        tokensIn: number;
        tokensOut: number;
        model: string;
        estimatedCost: number;
        currency: string;
    };
    tool?: ToolInfo;
}

export interface PendingConfirm {
    confirmId: string;
    toolCallId: string;
    tool: string;
    args: Record<string, unknown>;
    timeoutS: number;
    /** 她开口问的那句话（后端按人格模板渲染），弹窗用 */
    prompt?: string;
}

export interface UseChatResult {
    messages: Ref<ChatMessage[]>;
    isGenerating: Ref<boolean>;
    currentEmotion: Ref<Emotion>;
    /** 本轮接手的专家；run_started 之后、expert_selected 之前为 null */
    currentExpert: Ref<Expert | null>;
    pendingConfirm: Ref<PendingConfirm | null>;
    /** 后端历史消息总数（含工具消息） */
    historyTotal: Ref<number>;
    /** 是否还有更早的历史可加载 */
    hasMoreHistory: Ref<boolean>;
    sendText: (text: string) => void;
    resolveConfirm: (allowed: boolean) => void;
    loadHistory: () => Promise<void>;
    loadMoreHistory: () => Promise<void>;
    clearAll: () => Promise<void>;
    regenerate: () => void;
    clear: () => void;
}

let seq = 0;
function newId(): string {
    seq += 1;
    return `${Date.now()}-${seq}`;
}

// 情绪回落延时
const EMOTION_RESET_MS = 5000;

// 每次拉取的历史页大小
const HISTORY_PAGE_SIZE = 100;

/** 后端落库的工具结果状态 → 卡片状态；认不出来的一律当成功 */
function toToolStatus(status: string | null | undefined): ToolStatus {
    return status === "error" || status === "denied" ? status : "ok";
}

export function useChat(ws: UseWebSocketResult, tts: UseTtsResult): UseChatResult {
    const messages = ref<ChatMessage[]>([]);
    const isGenerating = ref(false);
    const currentEmotion = ref<Emotion>("neutral");
    const currentExpert = ref<Expert | null>(null);
    const pendingConfirm = ref<PendingConfirm | null>(null);
    const historyTotal = ref(0);
    const hasMoreHistory = ref(false);

    let streamingId: string | null = null;
    let emotionResetTimer: ReturnType<typeof setTimeout> | null = null;

    // 下一次请求的 offset。
    // 用后端原始条数累计（含被过滤掉的工具消息），不减去本地过滤的条数——
    // 否则翻页会错位、重复或漏读。
    let nextHistoryOffset = 0;

    // ---------- 情绪定时器 ----------

    function scheduleEmotionReset() {
        if (emotionResetTimer !== null) {
            clearTimeout(emotionResetTimer);
        }
        emotionResetTimer = setTimeout(() => {
            currentEmotion.value = "neutral";
            emotionResetTimer = null;
        }, EMOTION_RESET_MS);
    }

    function clearEmotionReset() {
        if (emotionResetTimer !== null) {
            clearTimeout(emotionResetTimer);
            emotionResetTimer = null;
        }
    }

    // ---------- 工具函数 ----------

    function appendMessage(msg: ChatMessage) {
        messages.value.push(msg);
    }

    function findMessage(id: string): ChatMessage | undefined {
        return messages.value.find((m) => m.id === id);
    }

    function findByToolCallId(toolCallId: string): ChatMessage | undefined {
        return messages.value.find((m) => m.tool?.toolCallId === toolCallId);
    }

    /**
     * 把还挂在「执行中 / 待阁下示下」的工具卡片落到终态。
     */
    function finishStuckToolCards(reason: string) {
        for (const msg of messages.value) {
            if (msg.role !== "tool" || !msg.tool) continue;
            if (msg.tool.status === "running" || msg.tool.status === "confirm") {
                msg.tool.status = "error";
                msg.tool.error = reason;
            }
        }
    }

    function finishCurrentAssistant() {
        if (!streamingId) return;
        const msg = findMessage(streamingId);
        if (msg) msg.streaming = false;
        streamingId = null;
    }

    // ---------- 助手文本 ----------

    function handleRunStarted() {
        isGenerating.value = true;
        streamingId = null;
        currentExpert.value = null;
        currentEmotion.value = "neutral";
        clearEmotionReset();
    }

    function handleTextDelta(text: string) {
        if (!streamingId) {
            streamingId = newId();
            appendMessage({
                id: streamingId,
                role: "assistant",
                content: "",
                streaming: true,
                createdAt: Date.now(),
            });
        }
        const msg = findMessage(streamingId!);
        if (msg) {
            msg.content += text;
        }
    }

    function handleRunFinished(event: ServerEvent & { type: "run_finished" }) {
        isGenerating.value = false;
        if (streamingId) {
            const msg = findMessage(streamingId);
            if (msg) {
                msg.streaming = false;
                msg.stats = {
                    tokensIn: event.data.tokens_in,
                    tokensOut: event.data.tokens_out,
                    model: event.data.model,
                    estimatedCost: event.data.estimated_cost,
                    currency: event.data.currency,
                };
            }
        }
        streamingId = null;
        pendingConfirm.value = null;
        scheduleEmotionReset();
    }

    function handleError(data: ErrorData) {
        isGenerating.value = false;
        finishCurrentAssistant();

        const local = ERROR_MESSAGES[data.code];
        const detail = data.message?.trim() ?? "";
        const raw = data.code === "model_error" || data.code === "internal_error";
        const message = raw
            ? local && detail
                ? `${local}（${detail}）`
                : local || detail || "未知错误"
            : detail || local || "未知错误";

        finishStuckToolCards(message);
        pendingConfirm.value = null;

        appendMessage({
            id: newId(),
            role: "error",
            content: `[${data.code}] ${message}`,
            streaming: false,
            createdAt: Date.now(),
        });

        scheduleEmotionReset();
    }

    // ---------- 工具调用 ----------

    function handleToolCallStarted(event: ServerEvent & { type: "tool_call_started" }) {
        finishCurrentAssistant();

        const data = event.data;
        appendMessage({
            id: newId(),
            role: "tool",
            content: "",
            streaming: false,
            createdAt: Date.now(),
            tool: {
                toolCallId: data.tool_call_id,
                name: data.tool,
                args: data.args,
                status: "running",
            },
        });
    }

    function handleToolConfirmRequest(event: ServerEvent & { type: "tool_confirm_request" }) {
        const data = event.data;
        const msg = findByToolCallId(data.tool_call_id);

        if (msg?.tool) {
            msg.tool.status = "confirm";
            msg.tool.confirmId = data.confirm_id;
            msg.tool.prompt = data.prompt;
            msg.tool.confirmDeadline = Date.now() + data.timeout_s * 1000;
        }

        pendingConfirm.value = {
            confirmId: data.confirm_id,
            toolCallId: data.tool_call_id,
            tool: data.tool,
            args: data.args,
            timeoutS: data.timeout_s,
            prompt: data.prompt,
        };
    }

    function handleToolResult(event: ServerEvent & { type: "tool_result" }) {
        const data = event.data;
        const msg = findByToolCallId(data.tool_call_id);

        if (msg?.tool) {
            msg.tool.status = data.ok ? "ok" : "error";
            msg.tool.content = data.content;
            msg.tool.error = data.error ?? undefined;
            msg.tool.meta = data.meta;
        }

        if (pendingConfirm.value?.toolCallId === data.tool_call_id) {
            pendingConfirm.value = null;
        }
    }

    function handleToolDenied(event: ServerEvent & { type: "tool_denied" }) {
        const data = event.data;
        const msg = findByToolCallId(data.tool_call_id);

        if (msg?.tool) {
            msg.tool.status = "denied";
            msg.tool.error = data.reason;
        }

        if (pendingConfirm.value?.toolCallId === data.tool_call_id) {
            pendingConfirm.value = null;
        }
    }

    function resolveConfirm(allowed: boolean) {
        const pending = pendingConfirm.value;
        if (!pending) return;

        const sent = ws.send({
            type: "tool_confirm",
            request_id: newId(),
            data: {
                confirm_id: pending.confirmId,
                allowed,
            },
        });

        if (!sent) {
            appendMessage({
                id: newId(),
                role: "error",
                content: "[未连接] 确认没能送出去，本轮已作废",
                streaming: false,
                createdAt: Date.now(),
            });
        }

        pendingConfirm.value = null;
    }

    // ---------- 发送 ----------

    function sendText(text: string) {
        const trimmed = text.trim();
        if (!trimmed) return;

        appendMessage({
            id: newId(),
            role: "user",
            content: trimmed,
            streaming: false,
            createdAt: Date.now(),
        });

        const requestId = newId();

        const ok = ws.send({
            type: "text_input",
            request_id: requestId,
            data: { text: trimmed },
        });

        if (!ok) {
            appendMessage({
                id: newId(),
                role: "error",
                content: "[未连接] 消息未发送",
                streaming: false,
                createdAt: Date.now(),
            });
        }
    }

    // ---------- 重新生成 ----------

    function regenerate() {
        if (isGenerating.value) return;

        let lastUserIndex = -1;
        for (let i = messages.value.length - 1; i >= 0; i--) {
            if (messages.value[i].role === "user") {
                lastUserIndex = i;
                break;
            }
        }
        if (lastUserIndex < 0) return;

        const userText = messages.value[lastUserIndex].content;

        messages.value = messages.value.slice(0, lastUserIndex);

        sendText(userText);
    }

    // ---------- 历史 ----------

    /**
     * 把后端返回的一页历史映射成 ChatMessage[]。
     *
     * - 带 tool_calls 的 assistant 只是"要调工具"，实时渲染里也不出气泡，
     *   所以过滤掉；它提供的信息用来还原紧随其后的 tool 行。
     * - tool 行还原成 ToolCallCard 需要的形状。
     */
    function mapHistoryItems(items: import("@/api/history").HistoryMessage[]): ChatMessage[] {
        // tool_call_id → {name, args}：tool 行靠它还原出卡片
        const toolCallIndex = new Map<string, { name: string; args: Record<string, unknown> }>();
        for (const h of items) {
            for (const tc of h.tool_calls ?? []) {
                toolCallIndex.set(tc.id, {
                    name: tc.name,
                    args: tc.args,
                });
            }
        }

        const out: ChatMessage[] = [];
        for (const h of items) {
            // 带 tool_calls 的 assistant 不出气泡
            if (h.role === "assistant" && h.tool_calls?.length) {
                continue;
            }

            const msg: ChatMessage = {
                id: h.id,
                role: h.role,
                content: h.content,
                streaming: false,
                createdAt: new Date(h.created_at).getTime(),
            };

            if (h.role === "assistant" && (h.tokens_in || h.tokens_out)) {
                msg.stats = {
                    tokensIn: h.tokens_in,
                    tokensOut: h.tokens_out,
                    model: h.model,
                    estimatedCost: 0,
                    currency: "CNY",
                };
            }

            if (h.role === "tool") {
                const info = h.tool_call_id ? toolCallIndex.get(h.tool_call_id) : undefined;
                msg.tool = {
                    toolCallId: h.tool_call_id ?? "",
                    name: h.name ?? info?.name ?? "unknown",
                    args: info?.args ?? {},
                    status: toToolStatus(h.status),
                    content: h.content,
                };
            }

            out.push(msg);
        }
        return out;
    }

    async function loadHistory() {
        try {
            const res = await historyApi.list({
                limit: HISTORY_PAGE_SIZE,
                offset: 0,
            });
            messages.value = mapHistoryItems(res.items);
            nextHistoryOffset = res.items.length;
            historyTotal.value = res.total;
            hasMoreHistory.value = nextHistoryOffset < res.total;
        } catch (err) {
            console.warn("[chat] 加载历史失败：", err);
        }
    }

    async function loadMoreHistory() {
        if (!hasMoreHistory.value) return;

        try {
            const res = await historyApi.list({
                limit: HISTORY_PAGE_SIZE,
                offset: nextHistoryOffset,
            });
            const older = mapHistoryItems(res.items);
            messages.value = [...older, ...messages.value];
            nextHistoryOffset += res.items.length;
            historyTotal.value = res.total;
            hasMoreHistory.value = nextHistoryOffset < res.total;
        } catch (err) {
            console.warn("[chat] 加载更多历史失败：", err);
        }
    }

    async function clearAll() {
        try {
            await historyApi.clear();
        } catch (err) {
            console.warn("[chat] 清空后端历史失败：", err);
        }
        clear();
    }

    function clear() {
        messages.value = [];
        streamingId = null;
        isGenerating.value = false;
        pendingConfirm.value = null;
        currentExpert.value = null;
        currentEmotion.value = "neutral";
        clearEmotionReset();
        historyTotal.value = 0;
        hasMoreHistory.value = false;
        nextHistoryOffset = 0;
    }

    // ---------- 连接状态 ----------

    function resetInFlightTurn(reason: string) {
        const busy =
            isGenerating.value ||
            streamingId !== null ||
            pendingConfirm.value !== null ||
            messages.value.some(
                (m) =>
                    m.role === "tool" &&
                    (m.tool?.status === "running" || m.tool?.status === "confirm"),
            );
        if (!busy) return;

        finishCurrentAssistant();
        isGenerating.value = false;
        pendingConfirm.value = null;
        currentExpert.value = null;
        clearEmotionReset();

        finishStuckToolCards(reason);

        appendMessage({
            id: newId(),
            role: "error",
            content: `[连接中断] ${reason}`,
            streaming: false,
            createdAt: Date.now(),
        });
    }

    watch(ws.status, (status) => {
        if (status === "connected") return;
        resetInFlightTurn("连接已断开，本轮已中断");
    });

    // ---------- 事件订阅 ----------

    ws.on("run_started", () => handleRunStarted());

    ws.on("expert_selected", (e) => {
        if (e.type === "expert_selected") {
            currentExpert.value = e.data.expert;
        }
    });

    ws.on("emotion", (e) => {
        if (e.type === "emotion") {
            currentEmotion.value = e.data.emotion;
            clearEmotionReset();
        }
    });

    ws.on("text_delta", (e) => {
        if (e.type === "text_delta") {
            handleTextDelta(e.data.text);
        }
    });

    ws.on("run_finished", (e) => {
        if (e.type === "run_finished") {
            handleRunFinished(e);
        }
    });

    ws.on("error", (e) => {
        if (e.type === "error") {
            handleError(e.data);
        }
    });

    ws.on("server_shutdown", () => {
        appendMessage({
            id: newId(),
            role: "error",
            content: "[服务关闭] 后端即将关闭",
            streaming: false,
            createdAt: Date.now(),
        });
    });

    ws.on("tool_call_started", (e) => {
        if (e.type === "tool_call_started") {
            handleToolCallStarted(e);
        }
    });

    ws.on("tool_confirm_request", (e) => {
        if (e.type === "tool_confirm_request") {
            handleToolConfirmRequest(e);
        }
    });

    ws.on("tool_result", (e) => {
        if (e.type === "tool_result") {
            handleToolResult(e);
        }
    });

    ws.on("tool_denied", (e) => {
        if (e.type === "tool_denied") {
            handleToolDenied(e);
        }
    });

    // ---------- TTS ----------

    ws.on("tts_audio_chunk", (e) => {
        if (e.type === "tts_audio_chunk") {
            tts.enqueue(e.data.audio, e.data.format);
        }
    });

    ws.on("tts_finished", () => {
        // 队列机制会自动消耗，这里无需处理
    });

    return {
        messages,
        isGenerating,
        currentEmotion,
        currentExpert,
        pendingConfirm,
        historyTotal,
        hasMoreHistory,
        sendText,
        resolveConfirm,
        loadHistory,
        loadMoreHistory,
        clearAll,
        regenerate,
        clear,
    };
}
