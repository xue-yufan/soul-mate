import { computed, readonly, ref, type Ref } from "vue";
import { vi } from "vitest";

import type { UseChatResult } from "@/composables/useChat";
import type { UseTtsResult } from "@/composables/useTts";
import type { ConnectionStatus, UseWebSocketResult } from "@/composables/useWebSocket";
import type { ClientMessage, ServerEvent, TextInputPayload } from "@/types/protocol";

type EventHandler = (event: ServerEvent) => void;

export interface FakeWebSocket {
    result: UseWebSocketResult;
    status: Ref<ConnectionStatus>;
    sent: Array<Omit<ClientMessage, "v">>;
    send: ReturnType<typeof vi.fn>;
    emit: (event: ServerEvent) => void;
}

export function createFakeWebSocket(
    options: { sendResult?: boolean; status?: ConnectionStatus } = {},
): FakeWebSocket {
    const status = ref<ConnectionStatus>(options.status ?? "connected");
    const sent: Array<Omit<ClientMessage, "v">> = [];
    const handlers = new Map<string, Set<EventHandler>>();

    const send = vi.fn((payload: Omit<ClientMessage, "v">): boolean => {
        sent.push(payload);
        return options.sendResult ?? true;
    });

    const result: UseWebSocketResult = {
        status: readonly(status),
        connect: vi.fn(),
        disconnect: vi.fn(),
        send,
        on(type, handler) {
            let set = handlers.get(type);
            if (!set) {
                set = new Set();
                handlers.set(type, set);
            }
            set.add(handler);
            return () => {
                set.delete(handler);
            };
        },
    };

    return {
        result,
        status,
        sent,
        send,
        emit(event) {
            handlers.get(event.type)?.forEach((handler) => handler(event));
        },
    };
}

export function createFakeTts(): UseTtsResult {
    return {
        enabled: ref(true),
        isPlaying: ref(false),
        queueLength: computed(() => 0),
        mouth: { open: 0 },
        enqueue: vi.fn(),
        stop: vi.fn(),
        toggle: vi.fn(),
    };
}

export function createMockChat(overrides: Partial<UseChatResult> = {}): UseChatResult {
    return {
        messages: ref([]),
        isGenerating: ref(false),
        currentEmotion: ref("neutral"),
        currentExpert: ref(null),
        pendingConfirm: ref(null),
        historyTotal: ref(0),
        hasMoreHistory: ref(false),
        sendText: vi.fn(),
        resolveConfirm: vi.fn(),
        loadHistory: vi.fn(async () => {}),
        loadMoreHistory: vi.fn(async () => {}),
        clearAll: vi.fn(async () => {}),
        regenerate: vi.fn(),
        clear: vi.fn(),
        ...overrides,
    };
}

export function serverEvent<T extends ServerEvent["type"], D>(
    type: T,
    data: D,
): Extract<ServerEvent, { type: T }> {
    return {
        v: 1,
        type,
        request_id: "request-1",
        session_id: "default",
        ts: "2026-10-23T00:00:00.000Z",
        data,
    } as Extract<ServerEvent, { type: T }>;
}

export function textInput(sent: Array<Omit<ClientMessage, "v">>): TextInputPayload | undefined {
    return sent.find((message) => message.type === "text_input") as TextInputPayload | undefined;
}
