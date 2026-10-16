import { onUnmounted, readonly, ref, type Ref } from "vue";

import {
    PROTOCOL_VERSION,
    SERVER_EVENT_TYPES,
    isServerEvent,
    type ClientMessage,
    type ServerEvent,
} from "@/types/protocol";

type EventHandler = (event: ServerEvent) => void;

export type ConnectionStatus = "connecting" | "connected" | "disconnected" | "reconnecting";

export interface UseWebSocketResult {
    status: Readonly<Ref<ConnectionStatus>>;
    connect: (url: string) => void;
    disconnect: () => void;
    send: (payload: Omit<ClientMessage, "v">) => boolean;
    on: (type: ServerEvent["type"] | "*", handler: EventHandler) => () => void;
}

const HEARTBEAT_INTERVAL_MS = 30_000;
const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

export function useWebSocket(): UseWebSocketResult {
    const status = ref<ConnectionStatus>("disconnected");

    let ws: WebSocket | null = null;
    let url: string | null = null;
    let shouldReconnect = false;
    let reconnectDelay = RECONNECT_BASE_MS;
    let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const handlers = new Map<string, Set<EventHandler>>();

    function emit(event: ServerEvent) {
        const specific = handlers.get(event.type);
        if (specific) {
            for (const handler of specific) {
                handler(event);
            }
        }
    }

    function startHeartbeat() {
        stopHeartbeat();
        heartbeatTimer = setInterval(() => {
            send({ type: "ping" });
        }, HEARTBEAT_INTERVAL_MS);
    }

    function stopHeartbeat() {
        if (heartbeatTimer !== null) {
            clearInterval(heartbeatTimer);
            heartbeatTimer = null;
        }
    }

    function clearReconnectTimer() {
        if (reconnectTimer !== null) {
            clearTimeout(reconnectTimer);
            reconnectTimer = null;
        }
    }

    function scheduleReconnect() {
        if (!shouldReconnect || !url) {
            return;
        }
        clearReconnectTimer();
        status.value = "reconnecting";

        const delay = reconnectDelay;
        console.log(`[ws] ${delay}ms 后重连`);

        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            open(url!);
        }, delay);

        reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX_MS);
    }

    function open(target: string) {
        if (ws) {
            try {
                ws.close();
            } catch {
                // 忽略
            }
            ws = null;
        }

        status.value = "connecting";

        const socket = new WebSocket(target);
        ws = socket;

        socket.onopen = () => {
            if (ws !== socket) {
                return;
            }
            console.log("[ws] 已连接");
            status.value = "connected";
            reconnectDelay = RECONNECT_BASE_MS;
            startHeartbeat();
        };

        socket.onmessage = (event) => {
            if (ws !== socket) {
                return;
            }

            let parsed: unknown;
            try {
                parsed = JSON.parse(event.data);
            } catch {
                console.error("[ws] 非法 JSON：", event.data);
                return;
            }

            if (!isServerEvent(parsed)) {
                console.error("[ws] 非法事件：", parsed);
                return;
            }
            if (!SERVER_EVENT_TYPES.has(parsed.type)) {
                // 不影响运行，但这类事件不会再有人处理——早一点看见比事后猜好
                console.warn("[ws] 收到未登记的事件类型：", parsed.type);
            }
            emit(parsed);
        };

        socket.onerror = () => {
            if (ws !== socket) {
                return;
            }
            console.warn("[ws] 连接错误");
        };

        socket.onclose = (event) => {
            if (ws !== socket) {
                return;
            }
            console.log(`[ws] 已断开：code=${event.code}`);
            ws = null;
            stopHeartbeat();

            if (shouldReconnect) {
                scheduleReconnect();
            } else {
                status.value = "disconnected";
            }
        };
    }

    function connect(target: string) {
        url = target;
        shouldReconnect = true;
        reconnectDelay = RECONNECT_BASE_MS;
        open(target);
    }

    function disconnect() {
        shouldReconnect = false;
        clearReconnectTimer();
        stopHeartbeat();

        if (ws) {
            try {
                ws.close(1000, "client disconnect");
            } catch {
                // 忽略
            }
            ws = null;
        }
        status.value = "disconnected";
    }

    function send(payload: Omit<ClientMessage, "v">): boolean {
        if (!ws || ws.readyState !== WebSocket.OPEN) {
            console.warn("[ws] 未连接，丢弃消息：", payload.type);
            return false;
        }

        const message = {
            v: PROTOCOL_VERSION,
            ...payload,
        };

        try {
            ws.send(JSON.stringify(message));
            return true;
        } catch (err) {
            console.error("[ws] 发送失败：", err);
            return false;
        }
    }

    function on(type: ServerEvent["type"] | "*", handler: EventHandler): () => void {
        let set = handlers.get(type);
        if (!set) {
            set = new Set();
            handlers.set(type, set);
        }
        set.add(handler);

        return () => {
            set!.delete(handler);
            if (set!.size === 0) {
                handlers.delete(type);
            }
        };
    }

    onUnmounted(() => {
        disconnect();
    });

    return {
        status: readonly(status),
        connect,
        disconnect,
        send,
        on,
    };
}
