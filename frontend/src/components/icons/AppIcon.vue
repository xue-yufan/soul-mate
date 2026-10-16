<template>
    <svg
        class="app-icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.7"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
    >
        <!-- 爪印：一个掌垫 + 四个脚趾 -->
        <template v-if="name === 'paw'">
            <ellipse cx="12" cy="16.4" rx="4.6" ry="3.7" />
            <ellipse cx="6.6" cy="10.8" rx="1.9" ry="2.4" />
            <ellipse cx="10.1" cy="7.8" rx="1.9" ry="2.5" />
            <ellipse cx="14.6" cy="7.8" rx="1.9" ry="2.5" />
            <ellipse cx="18" cy="10.8" rx="1.9" ry="2.4" />
        </template>

        <!-- 喇叭：说话中 -->
        <template v-else-if="name === 'sound'">
            <path d="M4 9.4h3.3L11.6 6v12L7.3 14.6H4z" />
            <path d="M15 9.3a4.3 4.3 0 0 1 0 5.4" />
            <path d="M17.6 6.9a7.7 7.7 0 0 1 0 10.2" />
        </template>

        <!-- 喇叭：静音 -->
        <template v-else-if="name === 'mute'">
            <path d="M4 9.4h3.3L11.6 6v12L7.3 14.6H4z" />
            <path d="M15.4 10.2l4 3.6" />
            <path d="M19.4 10.2l-4 3.6" />
        </template>

        <!-- 书：摊开的一本，比 📚 那种彩色方块emoji 跟线性图标更搭 -->
        <template v-else-if="name === 'book'">
            <path d="M12 7.7v11.5" />
            <path
                d="M12 7.7C10.7 6.4 8.8 5.7 6.6 5.7c-.8 0-1.6.1-2.3.3v11.4c.7-.2 1.5-.3 2.3-.3 2.2 0 4.1.7 5.4 2"
            />
            <path
                d="M12 7.7c1.3-1.3 3.2-2 5.4-2 .8 0 1.6.1 2.3.3v11.4c-.7-.2-1.5-.3-2.3-.3-2.2 0-4.1.7-5.4 2"
            />
        </template>

        <!-- 齿轮：一圈轮齿 + 中间轴孔 -->
        <template v-else-if="name === 'gear'">
            <circle cx="12" cy="12" r="4.2" />
            <path d="M18 12h2.2" />
            <path d="M3.8 12H6" />
            <path d="M12 18v2.2" />
            <path d="M12 3.8V6" />
            <path d="M16.24 16.24l1.56 1.56" />
            <path d="M6.2 6.2l1.56 1.56" />
            <path d="M16.24 7.76l1.56-1.56" />
            <path d="M6.2 17.8l1.56-1.56" />
        </template>
    </svg>
</template>

<script setup lang="ts">
/**
 * 顶栏那一排图标。
 *
 * 原来是 🐾 / 🔊 / 📚 / ⚙ 混着用：三个是彩色 emoji（各自配色、各自笔触粗细），
 * 而 ⚙ 在 Windows 上会落到符号字体渲染成单色线稿，四个按钮摆一起风格不统一。
 * 换成同一套 24×24 线性图标后，线宽、大小一致，而且是 currentColor ——
 * 悬停/激活变色的时候图标跟着变（emoji 变不了）。
 */
defineProps<{
    name: "paw" | "sound" | "mute" | "book" | "gear";
}>();
</script>

<style scoped>
.app-icon {
    display: block;
    width: 16px;
    height: 16px;
}
</style>
