import js from "@eslint/js";
import prettierConfig from "eslint-config-prettier";
import pluginVue from "eslint-plugin-vue";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
    // 忽略
    {
        ignores: [
            "node_modules/**",
            "out/**",
            "release/**",
            "dist/**",
            ".vite/**",
            "public/**",
            "vendor/**",
            "_stage/**",
            // 桌宠：独立的 Electron 应用（自己的 package.json / node_modules，不进主项目构建）。
            // 它开始承载正式行为（订阅 WS、进打包流程）时把这行去掉，跑一次 lint --fix。
            "pet/**",
        ],
    },

    // 基础推荐规则
    js.configs.recommended,
    ...tseslint.configs.recommended,
    ...pluginVue.configs["flat/recommended"],

    // .vue 文件里 <script lang="ts"> 用 TS parser
    {
        files: ["**/*.vue"],
        languageOptions: {
            parserOptions: {
                parser: tseslint.parser,
            },
        },
    },

    // 全局变量（浏览器 + Node，因为 electron/ 目录）
    {
        languageOptions: {
            globals: {
                ...globals.browser,
                ...globals.node,
                // 构建时由 electron.vite.config.ts 注入（取自 package.json 的 version），
                // TS 侧的声明在 src/env.d.ts。不登记的话 no-undef 会误报：
                // 那是个构建期宏，不在任何运行时全局表里。
                __APP_VERSION__: "readonly",
            },
        },
    },

    // 项目放宽项
    {
        rules: {
            // 偶尔会用到 any，先警告不报错
            "@typescript-eslint/no-explicit-any": "warn",

            // 未使用变量：允许下划线开头
            "@typescript-eslint/no-unused-vars": [
                "warn",
                {
                    argsIgnorePattern: "^_",
                    varsIgnorePattern: "^_",
                    caughtErrorsIgnorePattern: "^_",
                },
            ],

            // Vue 组件名允许多词之外的写法（App.vue、MemoryTab.vue 等）
            "vue/multi-word-component-names": "off",

            // 允许 v-html（我们有 DOMPurify 净化）
            "vue/no-v-html": "off",
        },
    },

    // 最后关掉所有格式规则（交给 Prettier）
    prettierConfig,
);
