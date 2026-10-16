<template>
    <div v-if="open" class="modal-mask" @click.self="close">
        <div class="modal glass-surface">
            <header class="modal-header">
                <div class="tabs">
                    <button
                        v-for="tab in tabs"
                        :key="tab.key"
                        class="tab"
                        :class="{ active: activeTab === tab.key }"
                        @click="activeTab = tab.key"
                    >
                        {{ tab.label }}
                    </button>
                </div>
                <button class="close-btn" @click="close">×</button>
            </header>

            <div class="modal-body">
                <MemoryTab v-if="activeTab === 'memory'" :enabled="features.memory" />
                <DocumentsTab v-else-if="activeTab === 'documents'" :enabled="features.rag" />
                <WorldbookTab v-else-if="activeTab === 'worldbook'" :enabled="features.rag" />
                <AuditTab v-else-if="activeTab === 'audit'" :enabled="features.memory" />
            </div>
        </div>
    </div>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";

import AuditTab from "./AuditTab.vue";
import DocumentsTab from "./DocumentsTab.vue";
import MemoryTab from "./MemoryTab.vue";
import WorldbookTab from "./WorldbookTab.vue";

type TabKey = "memory" | "documents" | "worldbook" | "audit";

const props = defineProps<{
    open: boolean;
    features: {
        memory: boolean;
        rag: boolean;
    };
}>();

const emit = defineEmits<{
    (e: "update:open", value: boolean): void;
}>();

const tabs: { key: TabKey; label: string }[] = [
    { key: "memory", label: "记忆" },
    { key: "documents", label: "文档" },
    { key: "worldbook", label: "设定" },
    { key: "audit", label: "审计" },
];

const activeTab = ref<TabKey>("memory");

// 每次打开时根据功能开关选择默认 Tab
watch(
    () => props.open,
    (open) => {
        if (!open) {
            return;
        }
        if (props.features.memory) {
            activeTab.value = "memory";
        } else if (props.features.rag) {
            activeTab.value = "documents";
        } else {
            // 两个功能都关时，唯一有意义的是空审计页（虽然也会显示"未开启"）
            activeTab.value = "audit";
        }
    },
);

function close() {
    emit("update:open", false);
}
</script>

<style scoped>
.modal-mask {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
}

.modal {
    width: 720px;
    max-width: 90vw;
    height: 560px;
    max-height: 85vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;

    /* 玻璃底座来自全局 .glass-surface */
    --glass-radius: 16px;
    --glass-blur: 26px;
    --glass-tint: rgba(26, 20, 44, 0.62);
}

.modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 12px 0 20px;
    border-bottom: 1px solid var(--hairline-soft);
    min-height: 48px;
}

.tabs {
    display: flex;
    gap: 4px;
}

.tab {
    background: transparent;
    border: none;
    color: #8888aa;
    font-size: 14px;
    padding: 12px 16px;
    cursor: pointer;
    border-bottom: 2px solid transparent;
    transition:
        color 0.15s,
        border-color 0.15s;
}

.tab:hover {
    color: #b0b0c0;
}

.tab.active {
    color: #e8e8ec;
    border-bottom-color: var(--accent-surface-hover);
}

.close-btn {
    background: transparent;
    border: none;
    color: #8888aa;
    font-size: 24px;
    line-height: 1;
    cursor: pointer;
    padding: 0 8px;
}

.close-btn:hover {
    color: #e8e8ec;
}

.modal-body {
    flex: 1;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}
</style>
