<template>
    <div class="character-panel">
        <img
            class="character-image"
            :class="[`emotion-${emotion}`, { speaking, 'is-faded': stageReady }]"
            :src="imageSrc"
            alt="遐蝶"
            draggable="false"
        />

        <!--
            3D 舞台盖在立绘上。模型加载好之前（33MB，第一次要几秒）立绘还看得见，
            加载好之后立绘淡出；加载失败就一直用立绘，不白屏。
        -->
        <div class="character-stage-layer">
            <CharacterStage
                :emotion="emotion"
                :speaking="speaking"
                :mouth="mouth"
                @ready="stageReady = true"
                @failed="onStageFailed"
            />
        </div>
    </div>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";

import characterSrc from "@/assets/xiadie.png";
import CharacterStage from "@/components/character/CharacterStage.vue";
import type { MouthSignal } from "@/composables/useTts";
import type { Emotion } from "@/types/protocol";

const props = defineProps<{
    speaking: boolean;
    emotion: Emotion;
    /** useTts 的实时口型信号，直接透给 3D 舞台 */
    mouth: MouthSignal;
}>();

/** 3D 模型就位前，面板上顶的是这张立绘 */
const stageReady = ref(false);

function onStageFailed(reason: string): void {
    stageReady.value = false;
    console.warn("[character] 回退到立绘：", reason);
}

/**
 * 立绘路径。
 *
 * 这里走 Vite 的资源导入而不是写 "image/xiadie.png"：
 * 后者要求 public/image/ 下真有那个文件，之前 public/ 里只剩 Live2D 模型目录，
 * 于是立绘一直 404。导入的写法由构建器给出带 hash 的 URL，dev 和打包后都稳。
 *
 * 现在只有一张图（800×1279 竖构图），所有情绪共用；情绪差异暂时靠 CSS 滤镜。
 * 补图后把大图放 public/image/character/ 再改成映射更合适（不塞进 bundle）：
 *   happy: "image/character/happy.png",
 *   sad:   "image/character/sad.png",
 *   ...
 */
const EMOTION_IMAGE: Record<Emotion, string> = {
    neutral: characterSrc,
    happy: characterSrc,
    sad: characterSrc,
    surprised: characterSrc,
    shy: characterSrc,
    thinking: characterSrc,
    angry: characterSrc,
};

const imageSrc = computed(() => EMOTION_IMAGE[props.emotion]);
</script>

<style scoped>
.character-panel {
    position: relative;
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
}

/* 舞台铺满整个面板，浮在立绘上面 */
.character-stage-layer {
    position: absolute;
    inset: 0;
}

.character-image {
    /* 圆形取景。
       立绘是 800×1279 的不透明竖构图，铺在页面上就是一块硬边照片；
       裁成正方形再切圆，正好把头和肩收进圆里，边缘也不再是直角的。
       cover 保证填满圆且不变形，top center 让脸留在画面里。 */
    width: min(94%, 440px);
    aspect-ratio: 1 / 1;
    object-fit: cover;
    object-position: top center;
    border-radius: 50%;
    border: 1px solid rgba(167, 139, 250, 0.22);
    box-shadow: 0 0 44px rgba(120, 80, 200, 0.28);

    user-select: none;
    -webkit-user-drag: none;

    animation: breathe 4s ease-in-out infinite;
    transform-origin: center center;
    will-change: transform, filter;
    filter: drop-shadow(0 12px 40px rgba(120, 80, 200, 0.35))
        drop-shadow(0 4px 16px rgba(0, 0, 0, 0.5));
    transition:
        filter 0.6s ease,
        opacity 0.5s ease;
}

/* 3D 模型接上之后立绘退场（留在 DOM 里，万一渲染出问题还能马上露回来） */
.character-image.is-faded {
    opacity: 0;
    pointer-events: none;
}

/* ---------- 情绪：CSS 滤镜 ---------- */

/* neutral：默认 */

/* happy：暖色 + 提亮 + 提饱和 */
.character-image.emotion-happy {
    filter: drop-shadow(0 12px 44px rgba(255, 200, 140, 0.4))
        drop-shadow(0 4px 16px rgba(0, 0, 0, 0.5)) brightness(1.08) saturate(1.2) hue-rotate(-8deg);
}

/* sad：降饱和 + 变暗 + 冷色 */
.character-image.emotion-sad {
    filter: drop-shadow(0 12px 40px rgba(80, 100, 180, 0.4))
        drop-shadow(0 4px 16px rgba(0, 0, 0, 0.5)) brightness(0.85) saturate(0.55) hue-rotate(15deg);
}

/* surprised：提亮 + 强光晕 */
.character-image.emotion-surprised {
    filter: drop-shadow(0 12px 52px rgba(200, 180, 255, 0.65))
        drop-shadow(0 4px 16px rgba(0, 0, 0, 0.5)) brightness(1.12) saturate(1.1);
}

/* shy：暖粉 + 微降饱和 */
.character-image.emotion-shy {
    filter: drop-shadow(0 12px 44px rgba(255, 170, 200, 0.5))
        drop-shadow(0 4px 16px rgba(0, 0, 0, 0.5)) brightness(1.05) saturate(1.15)
        hue-rotate(-15deg);
}

/* thinking：降亮 + 降饱和 + 冷调 */
.character-image.emotion-thinking {
    filter: drop-shadow(0 12px 40px rgba(120, 130, 180, 0.4))
        drop-shadow(0 4px 16px rgba(0, 0, 0, 0.5)) brightness(0.92) saturate(0.75) hue-rotate(8deg);
}

/* angry：偏红 + 提饱和 */
.character-image.emotion-angry {
    filter: drop-shadow(0 12px 44px rgba(220, 80, 80, 0.5))
        drop-shadow(0 4px 16px rgba(0, 0, 0, 0.5)) brightness(1.02) saturate(1.3) hue-rotate(10deg);
}

/* speaking 叠加：只加动画，不动 filter，保留情绪滤镜 */
.character-image.speaking {
    animation: speaking 1.2s ease-in-out infinite;
}

@keyframes breathe {
    0%,
    100% {
        transform: scale(1) translateY(0);
    }
    50% {
        transform: scale(1.015) translateY(-4px);
    }
}

@keyframes speaking {
    0%,
    100% {
        transform: scale(1.02) translateY(0);
    }
    50% {
        transform: scale(1.045) translateY(-8px);
    }
}
</style>
