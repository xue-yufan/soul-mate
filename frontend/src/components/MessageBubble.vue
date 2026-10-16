<template>
    <div class="bubble-wrap" :class="`wrap-${message.role}`">
        <!-- 头像：助手侧 -->
        <div v-if="message.role === 'assistant'" class="avatar avatar-ai">
            <img :src="avatarSrc" alt="" />
        </div>

        <div class="bubble" :class="`bubble-${message.role}`">
            <div class="bubble-content">
                <div
                    v-if="isMarkdown"
                    class="bubble-text markdown-body"
                    v-html="renderedContent"
                ></div>
                <span v-else class="bubble-text">{{ message.content }}</span>
                <span v-if="message.streaming" class="cursor">▊</span>
            </div>

            <div class="bubble-meta">
                <span class="bubble-time">{{ timeText }}</span>

                <button
                    v-if="!message.streaming && message.content"
                    class="meta-btn"
                    :class="{ copied }"
                    @click="onCopy"
                >
                    {{ copied ? "已复制" : "复制" }}
                </button>

                <slot name="actions"></slot>
            </div>

            <div v-if="message.stats" class="bubble-stats">
                {{ message.stats.tokensIn }} → {{ message.stats.tokensOut }} ·
                {{ message.stats.estimatedCost.toFixed(4) }}
                {{ message.stats.currency }}
            </div>
        </div>

        <!-- 头像：用户侧 -->
        <div v-if="message.role === 'user'" class="avatar avatar-user">我</div>
    </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

import avatarSrc from "@/assets/xiadie.png";
import type { ChatMessage } from "@/composables/useChat";
import { renderMarkdown } from "@/utils/markdown";

const props = defineProps<{
    message: ChatMessage;
}>();

const copied = ref(false);

const isMarkdown = computed(() => props.message.role === "assistant" && !props.message.streaming);

const renderedContent = computed(() => {
    if (!isMarkdown.value) {
        return "";
    }
    return renderMarkdown(props.message.content);
});

const timeText = computed(() => {
    const d = new Date(props.message.createdAt);
    const now = new Date();
    const sameDay =
        d.getFullYear() === now.getFullYear() &&
        d.getMonth() === now.getMonth() &&
        d.getDate() === now.getDate();

    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");

    if (sameDay) {
        return `${hh}:${mm}`;
    }

    const mo = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    return `${mo}-${dd} ${hh}:${mm}`;
});

async function onCopy() {
    try {
        await navigator.clipboard.writeText(props.message.content);
        copied.value = true;
        setTimeout(() => {
            copied.value = false;
        }, 1500);
    } catch (err) {
        console.warn("[bubble] 复制失败：", err);
    }
}
</script>

<style scoped>
/* ---------- 外壳：头像 + 气泡 ---------- */

.bubble-wrap {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin-bottom: 16px;
    max-width: 88%;
}

.wrap-user {
    align-self: flex-end;
    flex-direction: row-reverse;
}

.wrap-assistant {
    align-self: flex-start;
}

.wrap-error {
    align-self: center;
    max-width: 100%;
}

/* ---------- 头像 ---------- */

.avatar {
    flex: 0 0 36px;
    width: 36px;
    height: 36px;
    border-radius: 50%;
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 600;
    color: #fff;
    user-select: none;
}

.avatar-ai {
    background: linear-gradient(135deg, rgba(167, 139, 250, 0.4), rgba(124, 92, 200, 0.4));
    border: 1px solid rgba(167, 139, 250, 0.3);
    box-shadow: 0 0 12px rgba(167, 139, 250, 0.25);
}

.avatar-ai img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: top center;
    transform: scale(1.6) translateY(15%);
}

.avatar-user {
    background: linear-gradient(135deg, rgba(80, 60, 140, 0.7), rgba(60, 45, 110, 0.7));
    border: 1px solid rgba(167, 139, 250, 0.2);
}

/* ---------- 气泡 ---------- */

.bubble {
    display: flex;
    flex-direction: column;
    min-width: 0;
}

.bubble-content {
    padding: 12px 16px;
    border-radius: 14px;
    font-size: 14px;
    line-height: 1.65;
    word-break: break-word;
}

/* 助手气泡：深紫霜面
   注意：这里是 .chat-panel（自带 backdrop-filter）的子孙，
   子孙的 backdrop-filter 只能采到 backdrop root 内部的内容，
   对气泡而言几乎是空操作，所以用"渐变 + 内高光"做出厚度感，不再烧 GPU。 */
.bubble-assistant .bubble-content {
    background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.07) 0%, rgba(255, 255, 255, 0.01) 100%),
        rgba(45, 35, 70, 0.62);
    border: 1px solid rgba(255, 255, 255, 0.09);
    color: #e8e8f0;
    border-top-left-radius: 4px;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.1),
        0 4px 14px rgba(0, 0, 0, 0.2);
}

/* 用户气泡：亮紫渐变 */
.bubble-user .bubble-content {
    background: linear-gradient(135deg, rgba(134, 102, 216, 0.62) 0%, rgba(90, 70, 170, 0.58) 100%);
    border: 1px solid rgba(196, 181, 253, 0.3);
    color: #f0eaff;
    border-top-right-radius: 4px;
    white-space: pre-wrap;
    box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.22),
        0 4px 16px rgba(124, 92, 200, 0.26);
}

/* 错误气泡 */
.bubble-error .bubble-content {
    background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.05) 0%, rgba(255, 255, 255, 0) 100%),
        rgba(80, 30, 40, 0.5);
    border: 1px solid rgba(224, 128, 128, 0.28);
    color: #e0a0a0;
    font-size: 12px;
    white-space: pre-wrap;
    text-align: center;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
}

.bubble-text {
    white-space: pre-wrap;
}

.bubble-text.markdown-body {
    white-space: normal;
}

.cursor {
    display: inline-block;
    margin-left: 2px;
    animation: blink 1s infinite;
    color: #a78bfa;
}

@keyframes blink {
    0%,
    50% {
        opacity: 1;
    }
    51%,
    100% {
        opacity: 0;
    }
}

/* ---------- 元信息 ---------- */

.bubble-meta {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 6px;
    padding: 0 4px;
    opacity: 0;
    transition: opacity 0.2s;
}

.bubble-wrap:hover .bubble-meta {
    opacity: 1;
}

.wrap-user .bubble-meta {
    justify-content: flex-end;
}

.bubble-time {
    font-size: 10px;
    color: #5a5a7a;
    letter-spacing: 0.5px;
}

.meta-btn {
    background: transparent;
    border: none;
    color: #7a7a9a;
    font-size: 10px;
    cursor: pointer;
    padding: 0;
    transition: color 0.15s;
}

.meta-btn:hover {
    color: #c4b5fd;
}

.meta-btn.copied {
    color: #7ec8a0;
}

.bubble-stats {
    margin-top: 4px;
    padding: 0 4px;
    font-size: 10px;
    color: #5a5a7a;
}

.wrap-user .bubble-stats {
    text-align: right;
}

/* ---------- Markdown 样式（保持不变） ---------- */

.bubble-content :deep(.markdown-body) {
    font-size: 14px;
    line-height: 1.65;
}

.bubble-content :deep(.markdown-body > *:first-child) {
    margin-top: 0;
}

.bubble-content :deep(.markdown-body > *:last-child) {
    margin-bottom: 0;
}

.bubble-content :deep(.markdown-body p) {
    margin: 0.5em 0;
}

.bubble-content :deep(.markdown-body h1),
.bubble-content :deep(.markdown-body h2),
.bubble-content :deep(.markdown-body h3),
.bubble-content :deep(.markdown-body h4) {
    margin: 0.8em 0 0.4em;
    font-weight: 600;
    line-height: 1.3;
    color: #e0d8ff;
}

.bubble-content :deep(.markdown-body h1) {
    font-size: 1.4em;
    border-bottom: 1px solid rgba(167, 139, 250, 0.2);
    padding-bottom: 0.3em;
}

.bubble-content :deep(.markdown-body h2) {
    font-size: 1.2em;
    border-bottom: 1px solid rgba(167, 139, 250, 0.2);
    padding-bottom: 0.2em;
}

.bubble-content :deep(.markdown-body h3) {
    font-size: 1.1em;
}

.bubble-content :deep(.markdown-body h4) {
    font-size: 1em;
}

.bubble-content :deep(.markdown-body ul),
.bubble-content :deep(.markdown-body ol) {
    margin: 0.5em 0;
    padding-left: 1.5em;
}

.bubble-content :deep(.markdown-body li) {
    margin: 0.2em 0;
}

.bubble-content :deep(.markdown-body li > p) {
    margin: 0;
}

.bubble-content :deep(.markdown-body blockquote) {
    margin: 0.5em 0;
    padding: 0.3em 1em;
    border-left: 3px solid rgba(167, 139, 250, 0.5);
    color: #b0a8d0;
    background: rgba(60, 45, 90, 0.3);
    border-radius: 0 6px 6px 0;
}

.bubble-content :deep(.markdown-body code) {
    padding: 0.15em 0.4em;
    background: rgba(15, 10, 25, 0.6);
    border: 1px solid rgba(167, 139, 250, 0.15);
    border-radius: 4px;
    font-family: ui-monospace, "Cascadia Code", "Consolas", monospace;
    font-size: 0.9em;
    color: #d0a0d0;
}

.bubble-content :deep(.markdown-body pre) {
    margin: 0.6em 0;
    padding: 12px 14px;
    background: rgba(10, 8, 20, 0.85);
    border: 1px solid rgba(167, 139, 250, 0.15);
    border-radius: 8px;
    overflow-x: auto;
    line-height: 1.5;
}

.bubble-content :deep(.markdown-body pre code) {
    padding: 0;
    background: transparent;
    border: none;
    color: #c8c8d8;
    font-size: 13px;
    white-space: pre;
}

.bubble-content :deep(.markdown-body a) {
    color: #a78bfa;
    text-decoration: none;
    border-bottom: 1px solid rgba(167, 139, 250, 0.4);
}

.bubble-content :deep(.markdown-body a:hover) {
    border-bottom-color: #a78bfa;
}

.bubble-content :deep(.markdown-body hr) {
    margin: 1em 0;
    border: none;
    border-top: 1px solid rgba(167, 139, 250, 0.15);
}

.bubble-content :deep(.markdown-body table) {
    margin: 0.6em 0;
    border-collapse: collapse;
    font-size: 13px;
    width: 100%;
}

.bubble-content :deep(.markdown-body th),
.bubble-content :deep(.markdown-body td) {
    padding: 6px 10px;
    border: 1px solid rgba(167, 139, 250, 0.15);
    text-align: left;
}

.bubble-content :deep(.markdown-body th) {
    background: rgba(40, 30, 60, 0.5);
    font-weight: 600;
}

.bubble-content :deep(.markdown-body img) {
    max-width: 100%;
    border-radius: 8px;
}

/* ---------- 代码高亮 ---------- */

.bubble-content :deep(.hljs) {
    color: #c8c8d8;
}

.bubble-content :deep(.hljs-keyword),
.bubble-content :deep(.hljs-selector-tag),
.bubble-content :deep(.hljs-built_in) {
    color: #d0a0d0;
}

.bubble-content :deep(.hljs-string),
.bubble-content :deep(.hljs-attr) {
    color: #a0d0a0;
}

.bubble-content :deep(.hljs-number),
.bubble-content :deep(.hljs-literal) {
    color: #d0a080;
}

.bubble-content :deep(.hljs-comment) {
    color: #666680;
    font-style: italic;
}

.bubble-content :deep(.hljs-function),
.bubble-content :deep(.hljs-title) {
    color: #a78bfa;
}

.bubble-content :deep(.hljs-variable),
.bubble-content :deep(.hljs-params) {
    color: #c8c8d8;
}

.bubble-content :deep(.hljs-type),
.bubble-content :deep(.hljs-class) {
    color: #d0d080;
}
</style>
