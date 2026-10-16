<template>
    <div class="tab-panel">
        <div v-if="!enabled" class="disabled-hint">记忆模块未开启（features.memory=false）</div>

        <template v-else>
            <div class="toolbar">
                <select v-model="actionFilter" class="select" @change="reload">
                    <option value="">全部动作</option>
                    <option value="update">更新</option>
                    <option value="delete_soft">软删</option>
                    <option value="delete_hard">硬删</option>
                    <option value="restore">恢复</option>
                </select>

                <button class="btn" :disabled="loading" @click="reload">
                    {{ loading ? "加载中..." : "刷新" }}
                </button>
                <span class="count">共 {{ total }} 条</span>
            </div>

            <div v-if="error" class="error">{{ error }}</div>

            <div class="list">
                <div v-if="items.length === 0 && !loading" class="empty">暂无审计记录</div>

                <div v-for="entry in filtered" :key="entry.id" class="entry">
                    <div class="entry-header" @click="toggle(entry.id)">
                        <span class="action-tag" :class="`action-${entry.action}`">
                            {{ actionLabel(entry.action) }}
                        </span>
                        <span class="target">{{ shortId(entry.target_id) }}</span>
                        <span class="time">{{ formatTime(entry.created_at) }}</span>
                        <span class="toggle">
                            {{ expandedId === entry.id ? "▲" : "▼" }}
                        </span>
                    </div>

                    <div v-if="expandedId === entry.id" class="entry-body">
                        <div v-if="entry.before" class="snapshot">
                            <div class="snapshot-title">变更前</div>
                            <pre class="snapshot-json">{{ pretty(entry.before) }}</pre>
                        </div>

                        <div v-if="entry.after" class="snapshot">
                            <div class="snapshot-title">变更后</div>
                            <pre class="snapshot-json">{{ pretty(entry.after) }}</pre>
                        </div>

                        <div v-if="!entry.before && !entry.after" class="snapshot-empty">
                            （无快照）
                        </div>

                        <div class="target-full">目标：{{ entry.target_id }}</div>
                    </div>
                </div>
            </div>
        </template>
    </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import { isApiError } from "@/api/http";
import { memoryApi } from "@/api/memory";
import type { AuditEntry } from "@/types/knowledge";

const props = defineProps<{
    enabled: boolean;
}>();

const items = ref<AuditEntry[]>([]);
const total = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);
const actionFilter = ref<string>("");
const expandedId = ref<number | null>(null);

// 后端没有 event_type/action 参数，本地过滤
const filtered = computed(() => {
    if (!actionFilter.value) return items.value;
    return items.value.filter((e) => e.action === actionFilter.value);
});

const ACTION_LABELS: Record<string, string> = {
    update: "更新",
    delete_soft: "软删",
    delete_hard: "硬删",
    restore: "恢复",
};

function actionLabel(action: string): string {
    return ACTION_LABELS[action] ?? action;
}

function shortId(id: string): string {
    return id.length > 12 ? `${id.slice(0, 12)}…` : id;
}

function pretty(obj: Record<string, unknown>): string {
    return JSON.stringify(obj, null, 2);
}

function toggle(id: number) {
    expandedId.value = expandedId.value === id ? null : id;
}

function formatTime(iso: string): string {
    try {
        const d = new Date(iso);
        return d.toLocaleString("zh-CN", {
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
        });
    } catch {
        return iso;
    }
}

async function reload() {
    if (!props.enabled) return;

    loading.value = true;
    error.value = null;

    try {
        const res = await memoryApi.audit({ limit: 200 });
        items.value = res.items;
        total.value = res.total;
        expandedId.value = null;
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
    } finally {
        loading.value = false;
    }
}

onMounted(reload);

watch(
    () => props.enabled,
    (v) => {
        if (v) void reload();
    },
);
</script>

<style scoped>
.tab-panel {
    display: flex;
    flex-direction: column;
    flex: 1;
    overflow: hidden;
}

.disabled-hint {
    margin: auto;
    color: #8888aa;
    font-size: 14px;
}

.toolbar {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 12px 20px;
    border-bottom: 1px solid var(--hairline-soft);
}

.select {
    background: var(--surface-raised);
    color: #e8e8ec;
    border: 1px solid var(--hairline);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    outline: none;
}

.select:focus {
    border-color: var(--accent-soft);
}

.count {
    margin-left: auto;
    color: #8888aa;
    font-size: 12px;
}

.error {
    padding: 8px 20px;
    background: var(--tint-danger);
    color: #e08080;
    font-size: 12px;
}

.list {
    flex: 1;
    overflow-y: auto;
    padding: 12px 20px;
}

.empty {
    text-align: center;
    padding: 40px;
    color: #555570;
    font-size: 14px;
}

.entry {
    margin-bottom: 8px;
    background: var(--surface-raised);
    border: 1px solid var(--hairline);
    border-radius: 8px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
    overflow: hidden;
}

.entry-header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 12px;
    cursor: pointer;
    transition: background 0.15s;
    font-size: 12px;
}

.entry-header:hover {
    background: rgba(255, 255, 255, 0.03);
}

.action-tag {
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    background: var(--surface-strong);
    color: #b0b0c0;
    flex: 0 0 auto;
}

.action-update {
    background: var(--tint-info);
    color: #80b0d0;
}

.action-delete_soft {
    background: var(--tint-warn);
    color: #d0b080;
}

.action-delete_hard {
    background: var(--tint-danger);
    color: #e08080;
}

.action-restore {
    background: var(--tint-success);
    color: #80d080;
}

.target {
    font-family: ui-monospace, monospace;
    color: #666680;
    font-size: 11px;
}

.time {
    margin-left: auto;
    color: #8888aa;
    font-size: 11px;
}

.toggle {
    color: #666680;
    font-size: 10px;
    flex: 0 0 auto;
}

.entry-body {
    padding: 10px 12px 12px;
    border-top: 1px solid var(--hairline-soft);
    background: var(--surface-sunken);
}

.snapshot {
    margin-bottom: 10px;
}

.snapshot:last-child {
    margin-bottom: 0;
}

.snapshot-title {
    font-size: 11px;
    color: #8888aa;
    margin-bottom: 4px;
    letter-spacing: 0.5px;
}

.snapshot-json {
    margin: 0;
    padding: 8px 10px;
    background: rgba(14, 9, 26, 0.5);
    border: 1px solid var(--hairline-soft);
    border-radius: 6px;
    font-family: ui-monospace, monospace;
    font-size: 11px;
    line-height: 1.5;
    color: #c0c0d0;
    white-space: pre-wrap;
    word-break: break-word;
    overflow-x: auto;
}

.snapshot-empty {
    font-size: 11px;
    color: #555570;
    font-style: italic;
    margin-bottom: 10px;
}

.target-full {
    font-family: ui-monospace, monospace;
    font-size: 10px;
    color: #555570;
    word-break: break-all;
}
</style>
