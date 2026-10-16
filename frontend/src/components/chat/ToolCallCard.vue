<template>
    <div class="tool-card" :class="`status-${tool.status}`">
        <div class="tool-header">
            <span class="tool-icon">{{ statusIcon }}</span>
            <span class="tool-name">{{ tool.name }}</span>
            <span class="tool-status">{{ statusText }}</span>
        </div>

        <div v-if="hasArgs" class="tool-args">
            <div v-for="(value, key) in tool.args" :key="key" class="arg-row">
                <span class="arg-key">{{ key }}</span>
                <span class="arg-value">{{ formatValue(value) }}</span>
            </div>
        </div>

        <div v-if="tool.status === 'confirm'" class="confirm-box">
            <p v-if="tool.prompt" class="confirm-ask">{{ tool.prompt }}</p>

            <div class="confirm-actions">
                <button class="btn btn-allow" @click="emit('resolve', true)">好，准了</button>
                <button class="btn btn-deny" @click="emit('resolve', false)">不必了</button>
            </div>

            <p v-if="remaining !== null" class="confirm-timer">
                若 {{ remaining }} 秒内没有回音，我便当阁下不愿。
            </p>
        </div>

        <div v-if="tool.content" class="tool-content">
            {{ tool.content }}
        </div>

        <div v-if="tool.error" class="tool-error">
            {{ tool.error }}
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";

import type { ChatMessage, ToolStatus } from "@/composables/useChat";

const props = defineProps<{
    message: ChatMessage;
}>();

const emit = defineEmits<{
    (e: "resolve", allowed: boolean): void;
}>();

const tool = computed(() => props.message.tool!);

// ---------- 确认倒计时 ----------

const remaining = ref<number | null>(null);
let timer: ReturnType<typeof setInterval> | null = null;

function stopTimer() {
    if (timer !== null) {
        clearInterval(timer);
        timer = null;
    }
}

function tick() {
    const deadline = props.message.tool?.confirmDeadline;
    if (!deadline) {
        remaining.value = null;
        return;
    }
    remaining.value = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
}

// 只有处于 confirm 状态才走计时；确认/拒绝后立刻停
watch(
    () => [props.message.tool?.status, props.message.tool?.confirmDeadline] as const,
    ([status, deadline]) => {
        stopTimer();
        if (status === "confirm" && deadline) {
            tick();
            timer = setInterval(tick, 1000);
            return;
        }
        remaining.value = null;
    },
    { immediate: true },
);

onUnmounted(stopTimer);

const hasArgs = computed(() => {
    const args = tool.value.args;
    return args && Object.keys(args).length > 0;
});

const statusIcon = computed(() => {
    switch (tool.value.status) {
        case "running":
            return "⏳";
        case "confirm":
            return "❓";
        case "ok":
            return "✅";
        case "error":
            return "❌";
        case "denied":
            return "🚫";
        default:
            // 未知状态：给个中性符号，别渲染成空白
            return "•";
    }
});

const statusText = computed(() => {
    const map: Record<ToolStatus, string> = {
        running: "执行中",
        confirm: "待阁下示下",
        ok: "完成",
        error: "失败",
        denied: "被拒绝",
    };
    return map[tool.value.status];
});

function formatValue(value: unknown): string {
    if (typeof value === "string") {
        return value;
    }
    if (value === null) {
        return "null";
    }
    if (value === undefined) {
        return "undefined";
    }
    try {
        return JSON.stringify(value);
    } catch {
        return String(value);
    }
}
</script>

<style scoped>
.tool-card {
    align-self: flex-start;
    max-width: 80%;
    margin-bottom: 12px;
    padding: 10px 14px;
    background: var(--surface-raised);
    border: 1px solid var(--hairline);
    border-left: 3px solid var(--accent-surface-hover);
    border-radius: 8px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
    font-size: 13px;
}

.tool-card.status-ok {
    border-left-color: #40a060;
}

.tool-card.status-error,
.tool-card.status-denied {
    border-left-color: #c05050;
}

.tool-card.status-confirm {
    border-left-color: #d0a040;
}

.tool-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
}

.tool-icon {
    font-size: 14px;
    line-height: 1;
}

.tool-name {
    font-family: ui-monospace, monospace;
    font-size: 13px;
    color: #a0a0d0;
    font-weight: 500;
}

.tool-status {
    margin-left: auto;
    font-size: 11px;
    color: #8888aa;
}

.tool-args {
    margin: 6px 0;
    padding: 6px 8px;
    background: var(--surface-sunken);
    border-radius: 4px;
}

.arg-row {
    display: flex;
    gap: 8px;
    font-size: 12px;
    font-family: ui-monospace, monospace;
    line-height: 1.5;
}

.arg-key {
    color: #8888aa;
    flex: 0 0 auto;
}

.arg-value {
    color: #c0c0d0;
    word-break: break-all;
}

.tool-content {
    margin-top: 6px;
    padding: 6px 8px;
    background: var(--surface-sunken);
    border-radius: 4px;
    font-size: 12px;
    color: #b0b0c0;
    white-space: pre-wrap;
    word-break: break-word;
}

/* 确认：她自己开口问一句，按钮跟在事实后面 */
.confirm-box {
    margin-top: 8px;
    padding: 8px 10px;
    background: var(--tint-warn);
    border: 1px solid rgba(208, 176, 128, 0.4);
    border-radius: 6px;
}

.confirm-ask {
    margin-bottom: 8px;
    font-size: 13px;
    line-height: 1.6;
    color: #e0d4b8;
}

.confirm-actions {
    display: flex;
    gap: 8px;
}

.btn {
    padding: 4px 12px;
    border: 1px solid var(--hairline);
    border-radius: 4px;
    background: var(--surface-strong);
    color: #e8e8ec;
    font-size: 12px;
    cursor: pointer;
    transition: background 0.15s;
}

.btn-allow {
    background: var(--tint-success);
    border-color: #3a7a3a;
    color: #a0e0a0;
}

.btn-allow:hover {
    background: rgba(128, 208, 128, 0.26);
}

.btn-deny {
    color: #b0b0c0;
}

.btn-deny:hover {
    background: var(--surface-stronger);
}

.confirm-timer {
    margin-top: 6px;
    font-size: 11px;
    color: #8888aa;
}

.tool-error {
    margin-top: 6px;
    padding: 6px 8px;
    background: var(--tint-danger);
    border-radius: 4px;
    font-size: 12px;
    color: #e08080;
    white-space: pre-wrap;
    word-break: break-word;
}
</style>
