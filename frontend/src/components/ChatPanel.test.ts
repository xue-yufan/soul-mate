import { nextTick } from "vue";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ChatPanel from "@/components/ChatPanel.vue";
import { createMockChat } from "@/test/helpers";

function mountPanel(chat = createMockChat()) {
    return mount(ChatPanel, {
        props: {
            chat,
            connectionStatus: "connected",
        },
        global: {
            stubs: {
                MessageBubble: true,
                ToolCallCard: true,
            },
        },
    });
}

describe("ChatPanel", () => {
    it("按 Enter 发送消息并清空输入框", async () => {
        const chat = createMockChat();
        const wrapper = mountPanel(chat);
        const input = wrapper.get("textarea");

        await input.setValue("测试消息");
        await input.trigger("keydown", {
            key: "Enter",
            shiftKey: false,
            isComposing: false,
        });

        expect(chat.sendText).toHaveBeenCalledWith("测试消息");
        expect((input.element as HTMLTextAreaElement).value).toBe("");
    });

    it("Shift+Enter 保留换行，不发送消息", async () => {
        const chat = createMockChat();
        const wrapper = mountPanel(chat);
        const input = wrapper.get("textarea");

        await input.setValue("第一行");
        await input.trigger("keydown", {
            key: "Enter",
            shiftKey: true,
            isComposing: false,
        });

        expect(chat.sendText).not.toHaveBeenCalled();
        expect((input.element as HTMLTextAreaElement).value).toBe("第一行");
    });

    it("生成过程中显示当前专家的等待提示", async () => {
        const chat = createMockChat();
        chat.isGenerating.value = true;
        chat.currentExpert.value = "info";

        const wrapper = mountPanel(chat);
        await nextTick();

        expect(wrapper.text()).toContain("正在查询信息");
        wrapper.unmount();
    });

    it("后端未连接时禁用输入框", () => {
        const chat = createMockChat();
        const wrapper = mount(ChatPanel, {
            props: {
                chat,
                connectionStatus: "disconnected",
            },
            global: {
                stubs: {
                    MessageBubble: true,
                    ToolCallCard: true,
                },
            },
        });

        expect(wrapper.get("textarea").attributes("disabled")).toBeDefined();
        expect(wrapper.get(".send-btn").attributes("disabled")).toBeDefined();
    });
});
