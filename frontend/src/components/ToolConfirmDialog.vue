<template>
    <div v-if="pending" class="modal-mask">
        <div
            class="confirm-modal glass-surface"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="tool-confirm-title"
        >
            <header class="modal-header">
                <span id="tool-confirm-title" class="modal-title"> 有待阁下示下 </span>
                <button class="close-btn" title="拒绝（Esc）" @click="deny">×</button>
            </header>

            <div class="modal-body">
                <p class="confirm-ask">{{ askText }}</p>

                <div class="tool-line">
                    <span class="tool-icon">🛠</span>
                    <span class="tool-name">{{ pending.tool }}</span>
                </div>

                <div v-if="hasArgs" class="tool-args">
                    <div v-for="(value, key) in pending.args" :key="key" class="arg-row">
                        <span class="arg-key">{{ key }}</span>
                        <span class="arg-value">{{ formatValue(value) }}</span>
                    </div>
                </div>
            </div>

            <footer class="modal-footer">
                <div v-if="remaining !== null && totalSeconds > 0" class="countdown">
                    <span class="countdown-text">
                        若 {{ remaining }} 秒内没有回音，我便当阁下不愿。
                    </span>
                    <div class="countdown-track">
                        <div class="countdown-bar" :style="{ width: `${percent}%` }"></div>
                    </div>
                </div>

                <div class="actions">
                    <button class="btn btn-deny" @click="deny">不必了</button>
                    <button class="btn btn-allow" @click="allow">好，准了</button>
                </div>
            </footer>
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import type { PendingConfirm } from "@/composables/useChat";

const props = defineProps<{
    pending: PendingConfirm | null;
}>();

const emit = defineEmits<{
    (e: "resolve", allowed: boolean): void;
}>();

// ---------- 倒计时 ----------
// 后端只下发 timeout_s，不给截止时间戳，所以用「弹窗拿到请求的时刻」自己推一个；
// 聊天流里的指令卡用的是同一套算法，两边显示的秒数不会打架。

const totalSeconds = computed(() => props.pending?.timeoutS ?? 0);

const remaining = ref<number | null>(null);
let deadline = 0;
let timer: ReturnType<typeof setInterval> | null = null;

function stopTimer() {
    if (timer !== null) {
        clearInterval(timer);
        timer = null;
    }
}

function tick() {
    if (deadline === 0) {
        remaining.value = null;
        return;
    }
    remaining.value = Math.max(0, Math.ceil((deadline - Date.now()) / 1000));
}

watch(
    () => [props.pending?.confirmId, props.pending?.timeoutS] as const,
    ([confirmId, timeoutS]) => {
        stopTimer();
        deadline = confirmId ? Date.now() + (timeoutS ?? 0) * 1000 : 0;
        tick();
        if (deadline !== 0) {
            // 250ms 一跳：秒数不用这么频繁，但进度条要跟得上
            timer = setInterval(tick, 250);
        }
    },
    { immediate: true },
);

onUnmounted(stopTimer);

/** 进度条宽度（%）；超时时间没配或已归零时画满 */
const percent = computed(() => {
    if (totalSeconds.value <= 0 || remaining.value === null) {
        return 100;
    }
    return Math.max(0, Math.min(100, (remaining.value / totalSeconds.value) * 100));
});

// ---------- 文案与参数 ----------

const askText = computed(() => {
    const pending = props.pending;
    if (!pending) {
        return "";
    }
    return pending.prompt?.trim() || `她想用一下「${pending.tool}」，阁下准是不准？`;
});

const hasArgs = computed(() => {
    const args = props.pending?.args;
    return !!args && Object.keys(args).length > 0;
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

// ---------- 表态 ----------
// 遮罩点击不做事：这是阻断式确认，误点不该等于拒绝；
// 只有两个按钮和 Esc 能表态，Esc 等同「不必了」。

function allow() {
    if (!props.pending) {
        return;
    }
    emit("resolve", true);
}

function deny() {
    if (!props.pending) {
        return;
    }
    emit("resolve", false);
}

function onKeydown(e: KeyboardEvent) {
    if (e.key !== "Escape" || !props.pending) {
        return;
    }
    e.preventDefault();
    deny();
}

onMounted(() => window.addEventListener("keydown", onKeydown));
onUnmounted(() => window.removeEventListener("keydown", onKeydown));
</script>

<style scoped>
.modal-mask {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    /* 压住设置 / 知识面板（它们都是 1000） */
    z-index: 1100;
}

.confirm-modal {
    width: 460px;
    max-width: 92vw;
    max-height: 82vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;

    /* 玻璃底座来自全局 .glass-surface */
    --glass-radius: 16px;
    --glass-blur: 26px;
    --glass-tint: rgba(26, 20, 44, 0.72);
}

.modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 14px 20px 6px;
}

.modal-title {
    font-size: 15px;
    font-weight: 600;
    color: #e8d8b8;
}

.close-btn {
    background: transparent;
    border: none;
    color: #8888aa;
    font-size: 24px;
    line-height: 1;
    cursor: pointer;
    padding: 0 4px;
}

.close-btn:hover {
    color: #e8e8ec;
}

/* ---------- 主体 ---------- */

.modal-body {
    flex: 1;
    overflow-y: auto;
    padding: 4px 20px 18px;
}

.confirm-ask {
    font-size: 14px;
    line-height: 1.7;
    color: #e0d4b8;
}

.tool-line {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 14px;
}

.tool-icon {
    font-size: 14px;
    line-height: 1;
}

.tool-name {
    font-family: ui-monospace, monospace;
    font-size: 13px;
    font-weight: 500;
    color: #a0a0d0;
}

.tool-args {
    margin-top: 8px;
    padding: 8px 10px;
    background: var(--surface-sunken);
    border-radius: 6px;
    max-height: 34vh;
    overflow-y: auto;
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

/* ---------- 倒计时与表态 ---------- */

.modal-footer {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 12px 20px 16px;
    border-top: 1px solid var(--hairline-soft);
}

.countdown {
    display: flex;
    flex-direction: column;
    gap: 6px;
}

.countdown-text {
    font-size: 11px;
    color: #8888aa;
}

.countdown-track {
    height: 2px;
    background: var(--surface-strong);
    border-radius: 2px;
    overflow: hidden;
}

.countdown-bar {
    height: 100%;
    background: linear-gradient(90deg, rgba(224, 176, 128, 0.5) 0%, rgba(224, 176, 128, 0.85) 100%);
    transition: width 0.25s linear;
}

.actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
}

.btn {
    padding: 7px 16px;
    border: 1px solid var(--hairline);
    border-radius: 6px;
    background: var(--surface-strong);
    color: #e8e8ec;
    font-size: 13px;
    cursor: pointer;
    transition: background 0.15s;
}

.btn-allow {
    background: var(--tint-success);
    border-color: rgba(128, 208, 128, 0.35);
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
</style>
