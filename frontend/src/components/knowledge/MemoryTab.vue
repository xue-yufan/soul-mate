<template>
    <div class="tab-panel">
        <div v-if="!enabled" class="disabled-hint">记忆模块未开启（features.memory=false）</div>

        <template v-else>
            <div class="toolbar">
                <select v-model="statusFilter" class="select" @change="reload">
                    <option value="active">活跃</option>
                    <option value="archived">归档</option>
                    <option value="deleted">已删除</option>
                    <option value="">全部</option>
                </select>

                <button class="btn" :disabled="loading" @click="reload">
                    {{ loading ? "加载中..." : "刷新" }}
                </button>

                <span class="count">共 {{ total }} 条</span>
            </div>

            <div v-if="error" class="error">{{ error }}</div>

            <div class="list">
                <div v-if="items.length === 0 && !loading" class="empty">暂无记忆</div>

                <div v-for="item in items" :key="item.id" class="item">
                    <div class="item-header">
                        <span class="badge" :class="`badge-${item.type}`">
                            {{ item.type }}
                        </span>
                        <span class="importance"> 重要性 {{ item.importance.toFixed(2) }} </span>
                        <span class="time">{{ formatTime(item.created_at) }}</span>
                    </div>

                    <div v-if="editingId === item.id" class="edit-area">
                        <textarea v-model="editContent" class="edit-input" rows="3" />
                        <div class="edit-actions">
                            <button class="btn btn-small" @click="saveEdit(item)">保存</button>
                            <button class="btn btn-small" @click="cancelEdit">取消</button>
                        </div>
                    </div>

                    <div v-else class="content">{{ item.content }}</div>

                    <div class="item-actions">
                        <button
                            v-if="item.status === 'active'"
                            class="link-btn"
                            @click="startEdit(item)"
                        >
                            编辑
                        </button>

                        <button
                            v-if="item.status === 'active'"
                            class="link-btn danger"
                            @click="removeItem(item)"
                        >
                            删除
                        </button>

                        <button
                            v-if="item.status === 'deleted'"
                            class="link-btn"
                            @click="restoreItem(item)"
                        >
                            恢复
                        </button>
                    </div>
                </div>
            </div>
        </template>
    </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from "vue";

import { memoryApi } from "@/api/memory";
import { isApiError } from "@/api/http";
import type { MemoryFact } from "@/types/knowledge";

const props = defineProps<{
    enabled: boolean;
}>();

const items = ref<MemoryFact[]>([]);
const total = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);
const statusFilter = ref<string>("active");

const editingId = ref<string | null>(null);
const editContent = ref("");

async function reload() {
    if (!props.enabled) return;

    loading.value = true;
    error.value = null;

    try {
        const res = await memoryApi.list({
            status: statusFilter.value || null,
            limit: 200,
        });
        items.value = res.items;
        total.value = res.total;
    } catch (err) {
        if (isApiError(err)) {
            error.value = err.message;
        } else {
            error.value = String(err);
        }
    } finally {
        loading.value = false;
    }
}

function startEdit(item: MemoryFact) {
    editingId.value = item.id;
    editContent.value = item.content;
}

function cancelEdit() {
    editingId.value = null;
    editContent.value = "";
}

async function saveEdit(item: MemoryFact) {
    const content = editContent.value.trim();
    if (!content) {
        return;
    }

    try {
        const updated = await memoryApi.update(item.id, { content });
        const idx = items.value.findIndex((x) => x.id === item.id);
        if (idx >= 0) {
            items.value[idx] = updated;
        }
        cancelEdit();
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
    }
}

async function removeItem(item: MemoryFact) {
    if (!confirm(`确定删除这条记忆？\n\n${item.content}`)) {
        return;
    }

    try {
        await memoryApi.remove(item.id);

        // 只有当前筛选不再包含它时，才从列表移除并调整总数。
        // 在"全部"筛选下，软删后条目仍在列表里（status 变成 deleted），
        // 不能移除，否则用户会以为它消失了。
        if (statusFilter.value === "active" || statusFilter.value === "archived") {
            items.value = items.value.filter((x) => x.id !== item.id);
            total.value -= 1;
        } else {
            item.status = "deleted";
        }
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
    }
}

async function restoreItem(item: MemoryFact) {
    try {
        const restored = await memoryApi.restore(item.id);

        if (statusFilter.value === "deleted") {
            items.value = items.value.filter((x) => x.id !== item.id);
            total.value -= 1;
        } else {
            // 全部筛选：原地更新状态
            const idx = items.value.findIndex((x) => x.id === item.id);
            if (idx >= 0) {
                items.value[idx] = restored;
            }
        }
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
    }
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
        });
    } catch {
        return iso;
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

.item {
    padding: 12px;
    margin-bottom: 10px;
    background: var(--surface-raised);
    border: 1px solid var(--hairline);
    border-radius: 8px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
}

.item-header {
    display: flex;
    gap: 10px;
    align-items: center;
    font-size: 11px;
    color: #8888aa;
    margin-bottom: 8px;
}

.badge {
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    background: var(--surface-strong);
    color: #b0b0c0;
}

.badge-preference {
    background: var(--tint-info);
    color: #80b0d0;
}

.badge-fact {
    background: var(--tint-success);
    color: #80d080;
}

.badge-event {
    background: var(--tint-warn);
    color: #d0b080;
}

.badge-emotion {
    background: var(--tint-pink);
    color: #d080b0;
}

.importance {
    margin-left: auto;
}

.time {
    font-size: 11px;
}

.content {
    font-size: 14px;
    line-height: 1.5;
    color: #e8e8ec;
    white-space: pre-wrap;
    word-break: break-word;
}

.item-actions {
    margin-top: 10px;
    display: flex;
    gap: 12px;
}

.link-btn {
    background: transparent;
    border: none;
    color: #8888cc;
    font-size: 12px;
    cursor: pointer;
    padding: 0;
    text-decoration: underline;
}

.link-btn:hover {
    color: #a0a0e0;
}

.link-btn.danger {
    color: #c08080;
}

.link-btn.danger:hover {
    color: #e0a0a0;
}

.edit-area {
    margin: 8px 0;
}

.edit-input {
    width: 100%;
    background: var(--surface-sunken);
    color: #e8e8ec;
    border: 1px solid var(--hairline);
    border-radius: 6px;
    padding: 8px 10px;
    font-family: inherit;
    font-size: 14px;
    line-height: 1.5;
    resize: vertical;
    outline: none;
}

.edit-input:focus {
    border-color: var(--accent-soft);
}

.edit-actions {
    margin-top: 8px;
    display: flex;
    gap: 8px;
}

.btn {
    background: var(--surface-strong);
    color: #e8e8ec;
    border: none;
    border-radius: 6px;
    padding: 6px 14px;
    font-size: 13px;
    cursor: pointer;
    transition: background 0.15s;
}

.btn:hover:not(:disabled) {
    background: var(--surface-stronger);
}

.btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.btn-small {
    padding: 4px 10px;
    font-size: 12px;
}
</style>
