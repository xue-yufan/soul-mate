// ---------- 契约版本 ----------

export const PROTOCOL_VERSION = 1 as const;

export type ProtocolVersion = typeof PROTOCOL_VERSION;

export function isSupportedVersion(v: unknown): v is ProtocolVersion {
    return v === PROTOCOL_VERSION;
}

// ---------- 信封 ----------

export interface Envelope<T extends string, D> {
    v: ProtocolVersion;
    type: T;
    request_id: string | null;
    session_id: string | null;
    ts: string;
    data: D;
}

// ---------- 客户端 → 服务端 ----------

export interface PingPayload {
    v: ProtocolVersion;
    type: "ping";
    request_id?: string;
    data?: Record<string, never>;
}

export interface TextInputPayload {
    v: ProtocolVersion;
    type: "text_input";
    request_id: string;
    data: { text: string };
}

export interface ToolConfirmPayload {
    v: ProtocolVersion;
    type: "tool_confirm";
    request_id: string;
    data: { confirm_id: string; allowed: boolean };
}

export type ClientMessage = PingPayload | TextInputPayload | ToolConfirmPayload;

// ---------- 服务端 → 客户端 ----------

// pong

export interface PongData {
    server_time: string;
}
export type PongEvent = Envelope<"pong", PongData>;

// echo

export interface EchoData {
    text: string;
    received_at: string;
}
export type EchoEvent = Envelope<"echo", EchoData>;

// run_started

export type RunStartedEvent = Envelope<"run_started", Record<string, never>>;

// emotion

export type Emotion = "neutral" | "happy" | "sad" | "surprised" | "shy" | "thinking" | "angry";

export interface EmotionData {
    emotion: Emotion;
}
export type EmotionEvent = Envelope<"emotion", EmotionData>;

// expert_selected
//
// 本轮接手的专家。后端在专家节点入口发一次，工具轮次回到专家时会再发一次。
// 从 run_started 到第一个 text_delta 之间可能隔着意图分类和工具调用，前端
// 靠它显示「正在查询信息…」这类等待提示——否则那段时间只能干等一个光标。

export type Expert = "chat" | "info";

export interface ExpertSelectedData {
    expert: Expert;
}
export type ExpertSelectedEvent = Envelope<"expert_selected", ExpertSelectedData>;

// text_delta

export interface TextDeltaData {
    text: string;
}
export type TextDeltaEvent = Envelope<"text_delta", TextDeltaData>;

// run_finished

export interface RunFinishedData {
    tokens_in: number;
    tokens_out: number;
    model: string;
    estimated_cost: number;
    currency: string;
}
export type RunFinishedEvent = Envelope<"run_finished", RunFinishedData>;

// tool_call_started

export interface ToolCallStartedData {
    request_id: string;
    tool_call_id: string;
    tool: string;
    args: Record<string, unknown>;
}
export type ToolCallStartedEvent = Envelope<"tool_call_started", ToolCallStartedData>;

// tool_confirm_request

export interface ToolConfirmRequestData {
    request_id: string;
    tool_call_id: string;
    confirm_id: string;
    tool: string;
    args: Record<string, unknown>;
    timeout_s: number;
    /** 后端按人格模板渲染的确认语，前端展示用 */
    prompt?: string;
}
export type ToolConfirmRequestEvent = Envelope<"tool_confirm_request", ToolConfirmRequestData>;

// tool_result

export interface ToolResultData {
    request_id: string;
    tool_call_id: string;
    tool: string;
    ok: boolean;
    content?: string;
    error?: string | null;
    meta?: Record<string, unknown>;
}
export type ToolResultEvent = Envelope<"tool_result", ToolResultData>;

// tool_denied

export interface ToolDeniedData {
    request_id: string;
    tool_call_id: string;
    tool: string;
    reason: string;
}
export type ToolDeniedEvent = Envelope<"tool_denied", ToolDeniedData>;

// tts_audio_chunk

export interface TtsAudioChunkData {
    /** base64 编码的 mp3 */
    audio: string;
    format: string;
    text: string;
    index: number;
}
export type TtsAudioChunkEvent = Envelope<"tts_audio_chunk", TtsAudioChunkData>;

// tts_finished

export interface TtsFinishedData {
    chunks: number;
    chars: number;
}
export type TtsFinishedEvent = Envelope<"tts_finished", TtsFinishedData>;

// error

export interface ErrorData {
    code: ErrorCode;
    message: string;
}
export type ErrorEvent = Envelope<"error", ErrorData>;

// server_shutdown

export interface ServerShutdownData {
    message: string;
}
export type ServerShutdownEvent = Envelope<"server_shutdown", ServerShutdownData>;

// ---------- 联合类型 ----------

export type ServerEvent =
    | PongEvent
    | EchoEvent
    | RunStartedEvent
    | ExpertSelectedEvent
    | EmotionEvent
    | TextDeltaEvent
    | RunFinishedEvent
    | ToolCallStartedEvent
    | ToolConfirmRequestEvent
    | ToolResultEvent
    | ToolDeniedEvent
    | TtsAudioChunkEvent
    | TtsFinishedEvent
    | ErrorEvent
    | ServerShutdownEvent;

/**
 * 服务端会发的事件类型全集
 *
 * 必须和上面的 ServerEvent 联合类型保持一致。`isServerEvent` 只看 `type` 是不是
 * 字符串，认不出的事件会被 emit() 静默丢掉——expert_selected 就这么漏了整整一个
 * 版本，前端一个报错都没有。运行时多一个少一个都留一条 console 警告。
 */
export const SERVER_EVENT_TYPES: ReadonlySet<string> = new Set([
    "pong",
    "echo",
    "run_started",
    "expert_selected",
    "emotion",
    "text_delta",
    "run_finished",
    "tool_call_started",
    "tool_confirm_request",
    "tool_result",
    "tool_denied",
    "tts_audio_chunk",
    "tts_finished",
    "error",
    "server_shutdown",
]);

// ---------- 错误码 ----------

export type ErrorCode =
    | "bad_json"
    | "bad_request"
    | "version_mismatch"
    | "unsupported_type"
    | "internal_error"
    | "model_error"
    | "recursion_limit"
    | "run_timeout"
    | "cancelled"
    | "tool_loop_limit"
    | "memory_unavailable"
    | "busy";

export const ERROR_MESSAGES: Record<ErrorCode, string> = {
    bad_json: "消息格式错误",
    bad_request: "请求参数不合法",
    version_mismatch: "版本不匹配，请更新应用",
    unsupported_type: "不支持的消息类型",
    internal_error: "服务异常，请重试",
    model_error: "模型暂时不可用",
    recursion_limit: "问题太复杂，请换个说法",
    run_timeout: "思考超时，请重试",
    cancelled: "已取消",
    tool_loop_limit: "工具调用次数超限",
    memory_unavailable: "记忆暂时不可用，本轮不带记忆",
    busy: "上一轮还在回复中",
};

// ---------- 辅助 ----------

export function isServerEvent(msg: unknown): msg is ServerEvent {
    if (typeof msg !== "object" || msg === null) return false;
    const m = msg as Record<string, unknown>;
    return typeof m.type === "string" && isSupportedVersion(m.v);
}
