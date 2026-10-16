import fs from "node:fs";
import path from "node:path";
import { app } from "electron";

/**
 * 使用者自己的那份配置
 *
 * 放在 app.getPath("userData")/settings.json：
 * - 不进安装目录，卸载重装不会被覆盖
 * - 不随包发布，所以不会把开发者的 API Key 带给别人
 *
 * 后端只认 config.local.yaml 和环境变量。config.local.yaml 是排除在打包外的，
 * 所以这里把 JSON 翻译成 SOUL_MATE_ 前缀的环境变量（pydantic-settings 的
 * 嵌套写法：SOUL_MATE_MODEL__API_KEY 对应 model.api_key），启动后端时下发，
 * 后端代码一行都不用改。
 */

export interface UserConfig {
    /** API 密钥；留空则后端没有密钥，聊天会失败 */
    apiKey?: string;
    /** 模型名，留空用后端默认 */
    model?: string;
    /** API 地址，留空用后端默认 */
    baseUrl?: string;
    /** 数据目录；留空时打包版用 userData/data，开发版沿用 backend/data */
    dataDir?: string;
    /** 功能开关；不写则用后端默认值 */
    features?: {
        memory?: boolean;
        rag?: boolean;
        tts?: boolean;
        tools?: boolean;
    };
}

const FILE_NAME = "settings.json";

// 首次运行生成的模板：默认只开"对话 + 语音 + 工具"。
// 记忆/RAG 需要本地嵌入模型（近 500MB 且要单独准备），给朋友的版本先关掉。
const TEMPLATE: UserConfig = {
    apiKey: "",
    model: "deepseek-chat",
    baseUrl: "https://api.deepseek.com",
    dataDir: "",
    features: {
        memory: false,
        rag: false,
        tts: true,
        tools: true,
    },
};

export function settingsFilePath(): string {
    return path.join(app.getPath("userData"), FILE_NAME);
}

/** 读用户配置；文件不存在时写一份模板出来，方便直接编辑 */
export function loadUserConfig(): UserConfig {
    const file = settingsFilePath();

    try {
        if (!fs.existsSync(file)) {
            fs.mkdirSync(path.dirname(file), { recursive: true });
            fs.writeFileSync(file, JSON.stringify(TEMPLATE, null, 2), "utf-8");
            console.log(`[config] 已生成用户配置模板：${file}`);
            return { ...TEMPLATE };
        }

        const parsed = JSON.parse(fs.readFileSync(file, "utf-8")) as UserConfig;
        console.log(`[config] 用户配置：${file}`);
        return parsed ?? {};
    } catch (err) {
        console.warn("[config] 读取用户配置失败，改用后端默认值：", err);
        return {};
    }
}

/**
 * 决定数据目录：用户没指定时，打包版落到 userData/data，开发版返回 null
 * （表示不动，继续用后端的 ./data，免得开发时历史数据"消失"）
 */
export function prepareDataDir(cfg: UserConfig): string | null {
    const custom = (cfg.dataDir ?? "").trim();
    const dir = custom || (app.isPackaged ? path.join(app.getPath("userData"), "data") : "");

    if (!dir) {
        return null;
    }

    try {
        fs.mkdirSync(dir, { recursive: true });
    } catch (err) {
        console.warn(`[config] 无法创建数据目录 ${dir}：`, err);
    }
    return dir;
}

/** 翻译成后端认的环境变量；空值一律不发，避免覆盖后端默认 */
export function userConfigToEnv(cfg: UserConfig): Record<string, string> {
    const env: Record<string, string> = {};

    if (cfg.apiKey && cfg.apiKey.trim()) {
        env.SOUL_MATE_MODEL__API_KEY = cfg.apiKey.trim();
    }
    if (cfg.model && cfg.model.trim()) {
        env.SOUL_MATE_MODEL__MODEL = cfg.model.trim();
    }
    if (cfg.baseUrl && cfg.baseUrl.trim()) {
        env.SOUL_MATE_MODEL__BASE_URL = cfg.baseUrl.trim();
    }

    const features = cfg.features;
    if (features) {
        const map: [keyof NonNullable<UserConfig["features"]>, string][] = [
            ["memory", "SOUL_MATE_FEATURES__MEMORY"],
            ["rag", "SOUL_MATE_FEATURES__RAG"],
            ["tts", "SOUL_MATE_FEATURES__TTS"],
            ["tools", "SOUL_MATE_FEATURES__TOOLS"],
        ];
        for (const [key, envName] of map) {
            const value = features[key];
            if (typeof value === "boolean") {
                env[envName] = String(value);
            }
        }
    }

    return env;
}
