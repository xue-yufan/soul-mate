<template>
    <div class="tab-panel">
        <div v-if="!enabled" class="disabled-hint">RAG 模块未开启（features.rag=false）</div>

        <template v-else>
            <div class="toolbar">
                <button class="btn btn-primary" @click="showImport = !showImport">
                    {{ showImport ? "取消" : "+ 粘贴文本" }}
                </button>

                <button class="btn" :disabled="importing" @click="onImportFromFile">
                    {{ importing ? "导入中..." : "从文件导入" }}
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
                <span class="count">共 {{ total }} 个文档</span>
            </div>

            <div v-if="showImport" class="import-form">
                <input v-model="importTitle" class="form-input" placeholder="文档标题" />
                <textarea
                    v-model="importContent"
                    class="form-textarea"
                    placeholder="粘贴文本内容..."
                    rows="5"
                />
                <div class="import-actions">
                    <button
                        class="btn btn-primary"
                        :disabled="!canImport || importing"
                        @click="doImport"
                    >
                        {{ importing ? "导入中..." : "导入" }}
                    </button>
                </div>
            </div>

            <div v-if="error" class="error">{{ error }}</div>

            <div class="list">
                <div v-if="items.length === 0 && !loading" class="empty">暂无文档</div>

                <div
                    v-for="item in items"
                    :key="item.id"
                    class="item"
                    :class="{ 'item-deleted': item.status === 'deleted' }"
                >
                    <div class="item-row">
                        <span class="title">
                            {{ item.title }}
                            <span v-if="item.status !== 'active'" class="status-tag">
                                {{ item.status === "deleted" ? "已删除" : "已归档" }}
                            </span>
                        </span>
                        <span class="meta">
                            {{ item.chunk_count }} 块 ·
                            {{ formatSize(item.size_bytes) }}
                        </span>
                    </div>

                    <div v-if="item.source" class="source">来源：{{ item.source }}</div>

                    <div class="item-actions">
                        <button
                            v-if="item.status === 'active'"
                            class="link-btn"
                            @click="toggleChunks(item)"
                        >
                            {{ expandedId === item.id ? "收起片段" : "查看片段" }}
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

                    <div v-if="expandedId === item.id" class="chunks">
                        <div v-if="chunksLoading" class="chunks-loading">加载中...</div>
                        <div v-for="c in chunks" :key="c.id" class="chunk">
                            <div class="chunk-index">#{{ c.index }}</div>
                            <div class="chunk-content">{{ c.content }}</div>
                        </div>
                    </div>
                </div>
            </div>
        </template>
    </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import { documentsApi } from "@/api/documents";
import { isApiError } from "@/api/http";
import type { DocumentChunk, DocumentItem } from "@/types/knowledge";

const props = defineProps<{
    enabled: boolean;
}>();

const items = ref<DocumentItem[]>([]);
const total = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);
const statusFilter = ref<string>("active");

const showImport = ref(false);
const importTitle = ref("");
const importContent = ref("");
const importing = ref(false);

const expandedId = ref<string | null>(null);
const chunks = ref<DocumentChunk[]>([]);
const chunksLoading = ref(false);

const canImport = computed(
    () => importTitle.value.trim() !== "" && importContent.value.trim() !== "",
);

async function reload() {
    if (!props.enabled) return;

    loading.value = true;
    error.value = null;

    try {
        const res = await documentsApi.list({
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

async function doImport() {
    if (!canImport.value || importing.value) return;

    importing.value = true;
    error.value = null;

    try {
        const doc = await documentsApi.importText({
            title: importTitle.value.trim(),
            content: importContent.value,
        });
        if (statusFilter.value === "active" || statusFilter.value === "") {
            items.value.unshift(doc);
            total.value += 1;
        }
        importTitle.value = "";
        importContent.value = "";
        showImport.value = false;
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
    } finally {
        importing.value = false;
    }
}

/**
 * 从本地文件导入。
 *
 * 走主进程 dialog，拿到绝对路径后交给后端读文件。
 * 选文件/导入都可能失败，全部走 error 提示。
 */
async function onImportFromFile() {
    if (importing.value) return;

    // 不写 = null：catch 里直接 return，这个初值永远不会被读到
    let path: string | null;
    try {
        path = await window.soulMate.openFileDialog();
    } catch (err) {
        error.value = `打开文件对话框失败：${err instanceof Error ? err.message : String(err)}`;
        return;
    }

    if (!path) return;

    importing.value = true;
    error.value = null;

    try {
        const doc = await documentsApi.importFile(path);
        if (statusFilter.value === "active" || statusFilter.value === "") {
            items.value.unshift(doc);
            total.value += 1;
        }
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
    } finally {
        importing.value = false;
    }
}

async function toggleChunks(item: DocumentItem) {
    if (expandedId.value === item.id) {
        expandedId.value = null;
        chunks.value = [];
        return;
    }

    expandedId.value = item.id;
    chunks.value = [];
    chunksLoading.value = true;

    try {
        chunks.value = await documentsApi.chunks(item.id);
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
    } finally {
        chunksLoading.value = false;
    }
}

async function removeItem(item: DocumentItem) {
    if (!confirm(`确定删除文档「${item.title}」？`)) {
        return;
    }

    try {
        await documentsApi.remove(item.id);

        if (statusFilter.value === "active" || statusFilter.value === "archived") {
            items.value = items.value.filter((x) => x.id !== item.id);
            total.value -= 1;
        } else {
            item.status = "deleted";
        }

        if (expandedId.value === item.id) {
            expandedId.value = null;
            chunks.value = [];
        }
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
    }
}

async function restoreItem(item: DocumentItem) {
    try {
        const restored = await documentsApi.update(item.id, {
            status: "active",
        });

        if (statusFilter.value === "active") {
            items.value = items.value.filter((x) => x.id !== item.id);
            total.value -= 1;
        } else {
            const idx = items.value.findIndex((x) => x.id === item.id);
            if (idx >= 0) {
                items.value[idx] = restored;
            }
        }
    } catch (err) {
        error.value = isApiError(err) ? err.message : String(err);
    }
}

function formatSize(bytes: number): string {
    if (bytes < 1024) {
        return `${bytes} B`;
    }
    if (bytes < 1024 * 1024) {
        return `${(bytes / 1024).toFixed(1)} KB`;
    }
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
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
    flex-wrap: wrap;
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

.import-form {
    padding: 12px 20px;
    background: var(--surface-sunken);
    border-bottom: 1px solid var(--hairline-soft);
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.form-input,
.form-textarea {
    background: var(--surface-raised);
    color: #e8e8ec;
    border: 1px solid var(--hairline);
    border-radius: 6px;
    padding: 8px 10px;
    font-family: inherit;
    font-size: 13px;
    outline: none;
}

.form-input:focus,
.form-textarea:focus {
    border-color: var(--accent-soft);
}

.form-textarea {
    resize: vertical;
    line-height: 1.5;
}

.import-actions {
    display: flex;
    justify-content: flex-end;
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

.item-row {
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

.meta {
    font-size: 12px;
    color: #8888aa;
}

.source {
    font-size: 11px;
    color: #666680;
    margin-bottom: 8px;
    word-break: break-all;
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

.chunks {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid var(--hairline-soft);
}

.chunks-loading {
    color: #8888aa;
    font-size: 12px;
}

.chunk {
    display: flex;
    gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid var(--hairline-soft);
}

.chunk:last-child {
    border-bottom: none;
}

.chunk-index {
    flex: 0 0 30px;
    color: #666680;
    font-size: 11px;
    font-family: ui-monospace, monospace;
}

.chunk-content {
    flex: 1;
    font-size: 12px;
    line-height: 1.5;
    color: #b0b0c0;
    white-space: pre-wrap;
    word-break: break-word;
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
