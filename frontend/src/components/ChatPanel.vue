<template>
    <div class="chat-panel glass-surface">
        <div class="chat-toolbar">
            <span class="toolbar-title">对话</span>
            <button
                class="toolbar-btn"
                :disabled="chat.messages.value.length === 0 || chat.isGenerating.value"
                @click="onClearAll"
            >
                清空
            </button>
        </div>

        <div ref="listRef" class="message-list">
            <div v-if="chat.hasMoreHistory.value" class="load-more">
                <button class="load-more-btn" @click="onLoadMore">加载更早的消息</button>
            </div>

            <template v-for="(msg, idx) in chat.messages.value" :key="msg.id">
                <ToolCallCard
                    v-if="msg.role === 'tool'"
                    :message="msg"
                    @resolve="chat.resolveConfirm"
                />
                <MessageBubble v-else :message="msg">
                    <template
                        v-if="msg.role === 'assistant' && !msg.streaming && isLastAssistant(idx)"
                        #actions
                    >
                        <button
                            class="meta-btn"
                            :disabled="chat.isGenerating.value"
                            @click="onRegenerate"
                        >
                            重新生成
                        </button>
                    </template>
                </MessageBubble>
            </template>

            <div v-if="chat.messages.value.length === 0" class="empty-hint">说点什么吧</div>

            <div v-if="waitingHint" class="waiting-hint">
                {{ waitingHint }}
            </div>
        </div>

        <div class="input-area">
            <textarea
                v-model="draft"
                class="input"
                placeholder="输入消息，Enter 发送，Shift+Enter 换行"
                rows="1"
                :disabled="!connected"
                @keydown="onKeydown"
            />
            <button class="send-btn" :disabled="!canSend" @click="onSend">
                {{ chat.isGenerating.value ? "生成中..." : "发送" }}
            </button>
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";

import MessageBubble from "./MessageBubble.vue";
import ToolCallCard from "./chat/ToolCallCard.vue";
import type { UseChatResult } from "@/composables/useChat";
import type { ConnectionStatus } from "@/composables/useWebSocket";

const props = defineProps<{
    chat: UseChatResult;
    connectionStatus: ConnectionStatus;
}>();

const draft = ref("");
const listRef = ref<HTMLElement | null>(null);

// 加载更早的消息时不要滚到底部——用户正在往回翻
let skipNextScroll = false;

const connected = computed(() => props.connectionStatus === "connected");

const canSend = computed(
    () => connected.value && !props.chat.isGenerating.value && draft.value.trim() !== "",
);

function onSend() {
    if (!canSend.value) return;
    props.chat.sendText(draft.value);
    draft.value = "";
}

function onKeydown(e: KeyboardEvent) {
    if (e.key !== "Enter" || e.shiftKey) return;
    // 中文输入法正在选词：这一次回车是"上屏"，不是"发送"
    if (e.isComposing || e.keyCode === 229) return;

    e.preventDefault();
    onSend();
}

async function onClearAll() {
    if (props.chat.messages.value.length === 0) return;
    const ok = confirm("确定清空全部对话？\n\n" + "这会删除历史消息和会话摘要，不影响长期记忆。");
    if (!ok) return;
    await props.chat.clearAll();
}

async function onLoadMore() {
    skipNextScroll = true;
    await props.chat.loadMoreHistory();
}

function onRegenerate() {
    props.chat.regenerate();
}

/**
 * 她还在忙、但屏幕上还没有任何动静时的等待提示。
 */
const waitingHint = computed(() => {
    if (!props.chat.isGenerating.value) return null;

    const list = props.chat.messages.value;

    if (list.some((m) => m.role === "assistant" && m.streaming)) {
        return null;
    }

    const last = list[list.length - 1];
    if (
        last?.role === "tool" &&
        (last.tool?.status === "running" || last.tool?.status === "confirm")
    ) {
        return null;
    }

    switch (props.chat.currentExpert.value) {
        case "info":
            return "正在查询信息…";
        case "chat":
            return "正在斟酌…";
        default:
            return "正在思考…";
    }
});

function isLastAssistant(idx: number): boolean {
    for (let i = idx + 1; i < props.chat.messages.value.length; i++) {
        if (props.chat.messages.value[i].role === "assistant") {
            return false;
        }
    }
    return true;
}

function scrollToBottom() {
    if (skipNextScroll) {
        skipNextScroll = false;
        return;
    }
    nextTick(() => {
        if (listRef.value) {
            listRef.value.scrollTop = listRef.value.scrollHeight;
        }
    });
}

watch(() => props.chat.messages.value.length, scrollToBottom);
watch(() => props.chat.messages.value.map((m) => m.content).join("|"), scrollToBottom);
watch(waitingHint, scrollToBottom);
</script>

<style scoped>
.chat-panel {
    display: flex;
    flex-direction: column;
    flex: 1;
    height: 100%;
    overflow: hidden;

    --glass-blur: 30px;
    --glass-tint: rgba(25, 18, 42, 0.44);
    --glass-radius: 18px;
}

/* ---------- 工具栏 ---------- */

.chat-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 12px 20px;
    border-bottom: 1px solid rgba(255, 255, 255, 0.07);
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
}

.toolbar-title {
    font-size: 12px;
    color: #8a8aaa;
    letter-spacing: 1.5px;
    text-transform: uppercase;
}

.toolbar-btn {
    background: transparent;
    color: #8a8aaa;
    border: 1px solid rgba(167, 139, 250, 0.15);
    border-radius: 8px;
    padding: 4px 12px;
    font-size: 11px;
    cursor: pointer;
    transition: all 0.2s;
}

.toolbar-btn:hover:not(:disabled) {
    color: #e0a0a0;
    border-color: rgba(224, 128, 128, 0.4);
    background: rgba(80, 40, 60, 0.3);
}

.toolbar-btn:disabled {
    opacity: 0.35;
    cursor: not-allowed;
}

/* ---------- 消息列表 ---------- */

.message-list {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
    display: flex;
    flex-direction: column;
    scrollbar-width: thin;
    scrollbar-color: rgba(167, 139, 250, 0.2) transparent;
}

.message-list::-webkit-scrollbar {
    width: 6px;
}

.message-list::-webkit-scrollbar-track {
    background: transparent;
}

.message-list::-webkit-scrollbar-thumb {
    background: rgba(167, 139, 250, 0.2);
    border-radius: 3px;
}

.message-list::-webkit-scrollbar-thumb:hover {
    background: rgba(167, 139, 250, 0.35);
}

.load-more {
    display: flex;
    justify-content: center;
    padding: 4px 0 16px;
}

.load-more-btn {
    background: transparent;
    border: 1px solid rgba(255, 255, 255, 0.1);
    color: #8a8aaa;
    font-size: 12px;
    padding: 4px 16px;
    border-radius: 12px;
    cursor: pointer;
    transition:
        border-color 0.15s,
        color 0.15s;
}

.load-more-btn:hover {
    border-color: rgba(167, 139, 250, 0.5);
    color: #c4b5fd;
}

.empty-hint {
    margin: auto;
    color: #5a5a7a;
    font-size: 13px;
    letter-spacing: 1px;
}

.waiting-hint {
    align-self: flex-start;
    padding: 8px 14px;
    margin-bottom: 12px;
    border-radius: 14px;
    border-top-left-radius: 4px;
    background: rgba(45, 35, 70, 0.42);
    border: 1px solid rgba(255, 255, 255, 0.06);
    color: #9a9ac0;
    font-size: 13px;
    letter-spacing: 0.5px;
    animation: waiting-pulse 1.6s ease-in-out infinite;
}

@keyframes waiting-pulse {
    0%,
    100% {
        opacity: 0.55;
    }
    50% {
        opacity: 1;
    }
}

/* ---------- 输入区 ---------- */

.input-area {
    display: flex;
    gap: 10px;
    padding: 14px 16px;
    border-top: 1px solid rgba(255, 255, 255, 0.07);
    background: linear-gradient(180deg, rgba(12, 8, 22, 0.1) 0%, rgba(12, 8, 22, 0.42) 100%);
    border-radius: 0 0 var(--glass-radius) var(--glass-radius);
}

.input {
    flex: 1;
    background: rgba(14, 9, 26, 0.42);
    color: #e8e8f0;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 10px;
    padding: 10px 14px;
    font-family: inherit;
    font-size: 14px;
    line-height: 1.4;
    resize: none;
    outline: none;
    min-height: 42px;
    max-height: 120px;
    transition: all 0.2s;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
}

.input::placeholder {
    color: #5a5a7a;
}

.input:focus {
    border-color: rgba(167, 139, 250, 0.5);
    background: rgba(30, 20, 52, 0.55);
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.09),
        0 0 0 3px rgba(167, 139, 250, 0.12),
        0 0 18px rgba(167, 139, 250, 0.18);
}

.input:disabled {
    opacity: 0.4;
    cursor: not-allowed;
}

.send-btn {
    padding: 0 22px;
    background: linear-gradient(135deg, rgba(124, 92, 200, 0.9) 0%, rgba(90, 70, 180, 0.9) 100%);
    color: #fff;
    border: none;
    border-radius: 10px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.22),
        0 4px 16px rgba(124, 92, 200, 0.28);
}

.send-btn:hover:not(:disabled) {
    background: linear-gradient(135deg, rgba(144, 112, 220, 1) 0%, rgba(110, 90, 200, 1) 100%);
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.28),
        0 6px 22px rgba(124, 92, 200, 0.42);
    transform: translateY(-1px);
}

.send-btn:disabled {
    background: rgba(60, 50, 80, 0.5);
    color: #5a5a7a;
    cursor: not-allowed;
    box-shadow: none;
    transform: none;
}

/* ---------- 消息操作按钮 ---------- */

.meta-btn {
    background: transparent;
    border: none;
    color: #7a7a9a;
    font-size: 11px;
    cursor: pointer;
    padding: 0;
    transition: color 0.15s;
}

.meta-btn:hover:not(:disabled) {
    color: #c4b5fd;
}

.meta-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
}
</style>
