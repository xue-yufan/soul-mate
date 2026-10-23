<template>
    <div class="app">
        <!--
            背景层：毛玻璃的"被模糊物"。
            纯装饰、不吃事件，且不能带 filter / opacity / transform，
            否则会形成 backdrop root，玻璃面板就采样不到它了。
        -->
        <div class="backdrop" aria-hidden="true"></div>

        <header class="topbar">
            <span class="brand">
                <img class="brand-avatar" :src="characterSrc" alt="" draggable="false" />
                <span class="title">Soul Mate</span>
            </span>
            <div class="topbar-right">
                <button
                    class="icon-btn"
                    :class="{ active: camera.enabled.value }"
                    :disabled="
                        !camera.supported.value ||
                        ws.status.value !== 'connected' ||
                        configState.config.value?.features.vision === false
                    "
                    :title="cameraText"
                    @click="camera.toggle"
                >
                    <AppIcon name="camera" />
                </button>
                <button
                    class="icon-btn"
                    :class="{ active: petRunning }"
                    :title="petText"
                    @click="onTogglePet"
                >
                    <AppIcon name="paw" />
                </button>
                <button
                    class="icon-btn"
                    :class="{ active: tts.enabled.value }"
                    :title="tts.enabled.value ? '关闭语音' : '开启语音'"
                    @click="tts.toggle"
                >
                    <AppIcon :name="tts.enabled.value ? 'sound' : 'mute'" />
                </button>
                <button class="icon-btn" title="知识管理" @click="knowledgeOpen = true">
                    <AppIcon name="book" />
                </button>
                <button class="icon-btn" title="设置" @click="settingsOpen = true">
                    <AppIcon name="gear" />
                </button>
                <span class="status" :class="backendStatusClass">
                    {{ backendText }}
                </span>
                <span class="status" :class="wsStatusClass">
                    {{ wsText }}
                </span>
            </div>
        </header>

        <main class="main">
            <section class="character">
                <CharacterPanel
                    :speaking="tts.isPlaying.value"
                    :emotion="chat.currentEmotion.value"
                    :mouth="tts.mouth"
                />
            </section>

            <section class="chat">
                <ChatPanel :chat="chat" :connection-status="ws.status.value" />
            </section>
        </main>

        <footer class="statusbar">
            <span>
                {{ backendText }} · {{ wsText }} · {{ ttsText }} · {{ petText }} ·
                {{ camera.statusText.value }}
            </span>
            <span class="version">v{{ appVersion }}</span>
        </footer>

        <SettingsPanel
            v-model:open="settingsOpen"
            :local="localSettings"
            :config="configState"
            :backend="backend"
        />

        <KnowledgePanel v-model:open="knowledgeOpen" :features="knowledgeFeatures" />

        <ToolConfirmDialog :pending="chat.pendingConfirm.value" @resolve="chat.resolveConfirm" />
    </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import characterSrc from "@/assets/xiadie.png";
import ChatPanel from "@/components/ChatPanel.vue";
import CharacterPanel from "@/components/CharacterPanel.vue";
import SettingsPanel from "@/components/SettingsPanel.vue";
import ToolConfirmDialog from "@/components/ToolConfirmDialog.vue";
import AppIcon from "@/components/icons/AppIcon.vue";
import KnowledgePanel from "@/components/knowledge/KnowledgePanel.vue";
import { setBaseUrl } from "@/api/http";
import { useChat } from "@/composables/useChat";
import { useConfig } from "@/composables/useConfig";
import { useLocalSettings } from "@/composables/useLocalSettings";
import { useTts } from "@/composables/useTts";
import { useVisionCamera } from "@/composables/useVisionCamera";
import { useWebSocket } from "@/composables/useWebSocket";
import type { BackendSnapshot, PetSnapshot } from "@/types/electron";

// 构建时注入（见 electron.vite.config.ts），来源是 package.json
const appVersion = __APP_VERSION__;

// ---------- 后端进程状态 ----------

const backend = ref<BackendSnapshot>({
    status: "stopped",
    error: null,
    host: "127.0.0.1",
    port: 8000,
});

const backendText = computed(() => {
    switch (backend.value.status) {
        case "starting":
            return "后端启动中...";
        case "ready":
            return `后端 ${backend.value.host}:${backend.value.port}`;
        case "stopped":
            return "后端未启动";
        case "error":
            return `后端异常：${backend.value.error ?? "未知错误"}`;
        default:
            // TS 认为联合类型已经穷尽；这是给"以后后端加了新状态"兜底
            return "后端状态未知";
    }
});

const backendStatusClass = computed(() => `status-${backend.value.status}`);

// ---------- 桌宠 ----------
//
// 主应用是"启动器"：能拉起她、关掉她、显示状态，但不拥有她的生命周期
// （关掉主应用她还留着，见 electron/pet.ts）。

const petSnapshot = ref<PetSnapshot>({ status: "stopped", error: null });

const petRunning = computed(() => petSnapshot.value.status === "running");

const petText = computed(() => {
    switch (petSnapshot.value.status) {
        case "starting":
            return "桌宠启动中...";
        case "running":
            return "桌宠已运行";
        case "stopped":
            return "桌宠未运行";
        case "error":
            return `桌宠异常：${petSnapshot.value.error ?? "未知错误"}`;
        default:
            return "桌宠状态未知";
    }
});

async function onTogglePet() {
    if (petSnapshot.value.status === "starting") return;
    petSnapshot.value = petRunning.value
        ? await window.soulMate.stopPet()
        : await window.soulMate.startPet();
}

// ---------- 本地设置 + 后端配置 ----------

const localSettings = useLocalSettings();
const configState = useConfig();

// ---------- WebSocket + TTS + Chat ----------

const ws = useWebSocket();
const tts = useTts(localSettings.settings);
const chat = useChat(ws, tts);
const camera = useVisionCamera(ws);

const cameraText = computed(() => {
    if (configState.config.value?.features.vision === false) {
        return "视觉能力已在设置中关闭";
    }
    if (camera.error.value) {
        return `${camera.statusText.value}：${camera.error.value}`;
    }
    if (camera.enabled.value) {
        return "视觉感知已开启；她可以在需要时抓取一帧";
    }
    return "开启视觉感知；她会在需要时自主判断是否看一眼";
});

const wsText = computed(() => {
    switch (ws.status.value) {
        case "connecting":
            return "连接中...";
        case "connected":
            return "已连接";
        case "disconnected":
            return "未连接";
        case "reconnecting":
            return "重连中...";
        default:
            return "未知";
    }
});

const wsStatusClass = computed(() => {
    switch (ws.status.value) {
        case "connected":
            return "status-ready";
        case "connecting":
        case "reconnecting":
            return "status-starting";
        case "disconnected":
            return "status-stopped";
        default:
            // 未知状态：不套任何状态样式
            return "";
    }
});

const ttsText = computed(() => {
    if (!tts.enabled.value) return "语音关闭";
    if (tts.isPlaying.value) {
        const n = tts.queueLength.value;
        return n > 0 ? `播放中 (+${n})` : "播放中";
    }
    return "语音就绪";
});

// ---------- 面板开关 ----------

const settingsOpen = ref(false);
const knowledgeOpen = ref(false);

// ---------- 知识管理功能开关 ----------

const knowledgeFeatures = computed(() => {
    const cfg = configState.config.value;
    return {
        memory: cfg?.features.memory ?? false,
        rag: cfg?.features.rag ?? false,
    };
});

// ---------- 生命周期 ----------

let unsubscribeBackend: (() => void) | null = null;
let unsubscribePet: (() => void) | null = null;

onMounted(async () => {
    backend.value = await window.soulMate.getBackendStatus();
    unsubscribeBackend = window.soulMate.onBackendStatus((snapshot) => {
        backend.value = snapshot;
    });

    petSnapshot.value = await window.soulMate.getPetStatus();
    unsubscribePet = window.soulMate.onPetStatus((snapshot) => {
        petSnapshot.value = snapshot;
    });
});

onUnmounted(() => {
    unsubscribeBackend?.();
    unsubscribePet?.();
});

// 后端就绪时：
// - 连接 WebSocket
// - 设置 HTTP baseUrl
// - 拉公开配置
// - 加载会话历史
watch(
    () => backend.value.status,
    (status) => {
        if (status === "ready") {
            const { host, port } = backend.value;
            const base = `http://${host}:${port}`;
            ws.connect(`ws://${host}:${port}/ws`);
            setBaseUrl(base);
            void configState.refresh(base);
            void chat.loadHistory();
        }
    },
    { immediate: true },
);

// 打开设置面板时刷新配置
watch(settingsOpen, (open) => {
    if (open && backend.value.status === "ready") {
        const { host, port } = backend.value;
        void configState.refresh(`http://${host}:${port}`);
    }
});

// 打开知识面板时刷新配置
watch(knowledgeOpen, (open) => {
    if (open && backend.value.status === "ready") {
        const { host, port } = backend.value;
        void configState.refresh(`http://${host}:${port}`);
    }
});
</script>
