<template>
    <div class="tab-panel">
        <div v-if="!enabled" class="disabled-hint">RAG 模块未开启（features.rag=false）</div>

        <template v-else>
            <div class="toolbar">
                <button
                    v-if="statusFilter !== 'deleted'"
                    class="btn btn-primary"
                    @click="startCreate"
                >
                    + 新建条目
                </button>

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
                <div v-if="items.length === 0 && !loading && !editing" class="empty">
                    暂无设定条目
                </div>

                <div v-if="editing" class="item editing">
                    <div class="form-row">
                        <input v-model="editForm.title" class="form-input" placeholder="条目名" />
                    </div>
                    <div class="form-row">
                        <input
                            v-model="editForm.keywordsText"
                            class="form-input"
                            placeholder="关键词，用逗号分隔"
                        />
                    </div>
                    <div class="form-row">
                        <textarea
                            v-model="editForm.content"
                            class="form-textarea"
                            placeholder="内容"
                            rows="4"
                        />
                    </div>
                    <div class="form-row inline">
                        <label class="label">重要性</label>
                        <input
                            v-model.number="editForm.importance"
                            type="number"
                            class="form-input small"
                            min="0"
                            max="1"
                            step="0.1"
                        />
                    </div>
                    <div class="edit-actions">
                        <button class="btn btn-primary" :disabled="!canSave" @click="save">
                            保存
                        </button>
                        <button class="btn" @click="cancelEdit">取消</button>
                    </div>
                </div>

                <div
                    v-for="item in items"
                    :key="item.id"
                    class="item"
                    :class="{ 'item-deleted': item.status === 'deleted' }"
                >
                    <div class="item-header">
                        <span class="title">
                            {{ item.title }}
                            <span v-if="item.status !== 'active'" class="status-tag">
                                {{ item.status === "deleted" ? "已删除" : "已归档" }}
                            </span>
                        </span>
                        <span class="importance">
                            {{ item.importance.toFixed(2) }}
                        </span>
                    </div>

                    <div v-if="item.keywords.length" class="keywords">
                        <span v-for="kw in item.keywords" :key="kw" class="keyword">
                            {{ kw }}
                        </span>
                    </div>

                    <div class="content">{{ item.content }}</div>

                    <div class="item-actions">
                        <button
                            v-if="item.status !== 'deleted'"
                            class="link-btn"
                            @click="startEdit(item)"
                        >
                            编辑
                        </button>

                        <button
                            v-if="item.status !== 'deleted'"
                            class="link-btn danger"
                            @click="removeItem(item)"
                        >
                            删除
                        </button>

                        <button
                            v-if="item.status !== 'active'"
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
import { computed, onMounted, ref, watch } from "vue";

import { isApiError } from "@/api/http";
import { worldbookApi } from "@/api/worldbook";
import type { WorldbookEntry } from "@/types/knowledge";

const props = defineProps<{
    enabled: boolean;
}>();

const items = ref<WorldbookEntry[]>([]);
const total = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);
const statusFilter = ref<string>("active");

// 编辑状态：null 表示未编辑；否则是编辑中的条目 id，"" 表示新建
const editingId = ref<string | null>(null);
const editing = computed(() => editingId.value !== null);

const editForm = ref({
    title: "",
    keywordsText: "",
    content: "",
    importance: 0.7,
});

const canSave = computed(
    () => editForm.value.title.trim() !== "" && editForm.value.content.trim() !== "",
);

async function reload() {
    if (!props.enabled) {
        return;
    }

    loading.value = true;
    error.value = null;

    try {
        const res = await worldbookApi.list({
            status: statusFilter.value || null,
            limit: 200,
        });
        items.value = res.items;
        total.value = res.total;
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
    } finally {
        loading.value = false;
    }
}

function startCreate() {
    editingId.value = "";
    editForm.value = {
        title: "",
        keywordsText: "",
        content: "",
        importance: 0.7,
    };
}

function startEdit(item: WorldbookEntry) {
    editingId.value = item.id;
    editForm.value = {
        title: item.title,
        keywordsText: item.keywords.join(", "),
        content: item.content,
        importance: item.importance,
    };
}

function cancelEdit() {
    editingId.value = null;
}

function parseKeywords(text: string): string[] {
    return text
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter((s) => s !== "");
}

async function save() {
    if (!canSave.value) {
        return;
    }

    const payload = {
        title: editForm.value.title.trim(),
        content: editForm.value.content,
        keywords: parseKeywords(editForm.value.keywordsText),
        importance: Math.max(0, Math.min(1, editForm.value.importance)),
    };

    try {
        if (editingId.value === "") {
            const created = await worldbookApi.create(payload);
            // 只有当前筛选能看到新条目时才前插
            if (statusFilter.value === "active" || statusFilter.value === "") {
                items.value.unshift(created);
                total.value += 1;
            }
        } else if (editingId.value) {
            const updated = await worldbookApi.update(editingId.value, payload);
            const idx = items.value.findIndex((x) => x.id === editingId.value);
            if (idx >= 0) {
                items.value[idx] = updated;
            }
        }
        editingId.value = null;
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
    }
}

async function removeItem(item: WorldbookEntry) {
    if (!confirm(`确定删除条目「${item.title}」？`)) return;

    try {
        await worldbookApi.remove(item.id);

        // 只有当前筛选不再包含它时，才从列表移除并调整总数
        if (statusFilter.value === "active" || statusFilter.value === "archived") {
            items.value = items.value.filter((x) => x.id !== item.id);
            total.value -= 1;
        } else {
            // 全部筛选：原地改成已删除，保留在列表里
            item.status = "deleted";
        }
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
    }
}

async function restoreItem(item: WorldbookEntry) {
    try {
        const restored = await worldbookApi.update(item.id, {
            status: "active",
        });

        if (statusFilter.value === "active") {
            // 当前只看活跃：恢复后它不再符合筛选，移除
            items.value = items.value.filter((x) => x.id !== item.id);
            total.value -= 1;
        } else {
            // 全部 / 已删除 / 归档：原地更新状态
            const idx = items.value.findIndex((x) => x.id === item.id);
            if (idx >= 0) {
                items.value[idx] = restored;
            }
        }
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
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

.item-deleted {
    opacity: 0.55;
}

.item.editing {
    background: var(--surface-sunken);
    border-color: var(--accent-soft);
}

.item-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 6px;
}

.title {
    font-size: 14px;
    font-weight: 500;
    color: #e8e8ec;
    display: flex;
    align-items: center;
    gap: 8px;
}

.status-tag {
    padding: 1px 6px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 400;
    background: var(--tint-warn);
    color: #d0b080;
}

.importance {
    font-size: 11px;
    color: #8888aa;
    font-family: ui-monospace, monospace;
}

.keywords {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-bottom: 8px;
}

.keyword {
    padding: 1px 6px;
    background: var(--surface-strong);
    border-radius: 4px;
    font-size: 11px;
    color: #b0b0c0;
}

.content {
    font-size: 13px;
    line-height: 1.5;
    color: #b0b0c0;
    white-space: pre-wrap;
    word-break: break-word;
    margin-bottom: 8px;
}

.item-actions {
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

.form-row {
    margin-bottom: 8px;
}

.form-row.inline {
    display: flex;
    align-items: center;
    gap: 10px;
}

.label {
    font-size: 12px;
    color: #8888aa;
}

.form-input,
.form-textarea {
    width: 100%;
    background: var(--surface-raised);
    color: #e8e8ec;
    border: 1px solid var(--hairline);
    border-radius: 6px;
    padding: 8px 10px;
    font-family: inherit;
    font-size: 13px;
    outline: none;
}

.form-input.small {
    width: 80px;
}

.form-input:focus,
.form-textarea:focus {
    border-color: var(--accent-soft);
}

.form-textarea {
    resize: vertical;
    line-height: 1.5;
}

.edit-actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
    margin-top: 8px;
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

.btn-primary {
    background: var(--accent-surface);
}

.btn-primary:hover:not(:disabled) {
    background: var(--accent-surface-hover);
}
</style>
