import { effectScope, nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChat, type UseChatResult } from "@/composables/useChat";
import {
    createFakeTts,
    createFakeWebSocket,
    serverEvent,
    textInput,
    type FakeWebSocket,
} from "@/test/helpers";

describe("useChat", () => {
    let ws: FakeWebSocket;
    let chat: UseChatResult;
    let scope: ReturnType<typeof effectScope>;

    beforeEach(() => {
        vi.useFakeTimers();
        ws = createFakeWebSocket();
        scope = effectScope();
        chat = scope.run(() => useChat(ws.result, createFakeTts()))!;
    });

    afterEach(() => {
        scope.stop();
        vi.clearAllTimers();
        vi.useRealTimers();
    });

    it("发送前去空白，并把 text_input 写入 WebSocket", () => {
        chat.sendText("  你好  ");

        expect(chat.messages.value).toHaveLength(1);
        expect(chat.messages.value[0]).toMatchObject({
            role: "user",
            content: "你好",
        });
        expect(textInput(ws.sent)?.data.text).toBe("你好");
    });

    it("忽略空消息", () => {
        chat.sendText("   \n\t ");

        expect(chat.messages.value).toEqual([]);
        expect(ws.send).not.toHaveBeenCalled();
    });

    it("流式拼接助手正文并在结束时写入统计", () => {
        chat.sendText("你好");
        ws.emit(serverEvent("run_started", {}));
        ws.emit(serverEvent("text_delta", { text: "你" }));
        ws.emit(serverEvent("text_delta", { text: "好" }));
        ws.emit(
            serverEvent("run_finished", {
                tokens_in: 12,
                tokens_out: 34,
                model: "deepseek-chat",
                estimated_cost: 0.01,
                currency: "CNY",
            }),
        );

        const assistant = chat.messages.value.find((message) => message.role === "assistant");
        expect(assistant).toMatchObject({
            content: "你好",
            streaming: false,
            stats: {
                tokensIn: 12,
                tokensOut: 34,
                model: "deepseek-chat",
            },
        });
        expect(chat.isGenerating.value).toBe(false);
    });

    it("工具确认通过后发送一次 tool_confirm", () => {
        ws.emit(
            serverEvent("tool_call_started", {
                request_id: "request-1",
                tool_call_id: "call-1",
                tool: "read_file",
                args: { path: "notes.txt" },
            }),
        );
        ws.emit(
            serverEvent("tool_confirm_request", {
                request_id: "request-1",
                tool_call_id: "call-1",
                confirm_id: "confirm-1",
                tool: "read_file",
                args: { path: "notes.txt" },
                timeout_s: 30,
                prompt: "可以读吗？",
            }),
        );

        expect(chat.pendingConfirm.value?.confirmId).toBe("confirm-1");
        chat.resolveConfirm(true);

        const confirm = ws.sent.find((message) => message.type === "tool_confirm");
        expect(confirm?.data).toEqual({
            confirm_id: "confirm-1",
            allowed: true,
        });
        expect(chat.pendingConfirm.value).toBeNull();
    });

    it("连接中断时收尾进行中的工具和助手消息", async () => {
        ws.emit(serverEvent("run_started", {}));
        ws.emit(serverEvent("text_delta", { text: "还没有说完" }));
        ws.status.value = "disconnected";
        await nextTick();

        expect(chat.isGenerating.value).toBe(false);
        expect(chat.messages.value.some((message) => message.streaming)).toBe(false);
        expect(chat.messages.value.at(-1)).toMatchObject({
            role: "error",
            content: "[连接中断] 连接已断开，本轮已中断",
        });
    });

    it("把模型错误转成可展示的错误气泡", () => {
        ws.emit(
            serverEvent("error", {
                code: "model_error",
                message: "upstream timeout",
            }),
        );

        expect(chat.messages.value.at(-1)?.content).toBe(
            "[model_error] 模型暂时不可用（upstream timeout）",
        );
    });
});
