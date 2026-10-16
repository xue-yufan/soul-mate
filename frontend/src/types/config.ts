/**
 * 后端 /config/public 的响应类型
 *
 * 与 backend/app/api/config.py 保持一致
 *
 * settings 段是可写字段的完整描述（分组 / 类型 / 取值范围 / 当前值），
 * 设置面板据此渲染表单，不再在组件里硬编码设置项。
 */

export type SettingsFieldKind = "bool" | "int" | "float" | "str" | "select" | "secret" | "lines";

export type SettingsFieldValue = string | number | boolean | string[] | null;

export interface SettingsField {
    path: string;
    label: string;
    /** 所属分组 id，对应 SettingsGroup.id */
    group: string;
    kind: SettingsFieldKind;
    help: string;
    unit: string;
    min: number | null;
    max: number | null;
    step: number | null;
    options: string[];
    /** 枚举值 → 界面文案；缺省时直接显示枚举值本身 */
    option_labels: Record<string, string>;
    value: SettingsFieldValue;
    /** 被哪个环境变量固定；非 null 时该字段不可改 */
    locked_by: string | null;
    /** 仅 secret 字段有：是否已经设置了值（值本身永不下发） */
    is_set?: boolean;
}

export interface SettingsGroup {
    id: string;
    title: string;
    description: string;
}

export interface SettingsSchema {
    groups: SettingsGroup[];
    fields: SettingsField[];
    /** overrides 文件路径，展示用 */
    overrides_file: string;
    /**
     * 这批设置里是否有需要重启才能生效的项
     *
     * 具体某次改动要不要重启，以 PATCH /config 响应的 restart_required 为准：
     * 后端会即时重建运行时，只有 lifespan 装配的那些键（功能开关、TTS 音色、
     * 嵌入模型目录）才真正需要重启。
     */
    restart_required: boolean;
}

export interface PublicConfig {
    app: {
        name: string;
        version: string;
        env: string;
    };
    contracts: {
        ws_event_version: number;
    };
    features: {
        memory: boolean;
        rag: boolean;
        tools: boolean;
        tts: boolean;
    };
    model: {
        provider: string;
        adapter: string;
        model: string;
        base_url: string;
        api_key_set: boolean;
    };
    cost: {
        budget: number;
        currency: string;
    };
    tts: {
        provider: string;
        voice: string;
        rate: string;
    };
    agent: {
        default_session_id: string;
        history_turns: number;
        max_tool_steps: number;
    };
    settings: SettingsSchema;
}

export interface ConfigPatchResult {
    ok: boolean;
    /** 本次改动的字段路径 */
    updated: string[];
    /** 本次恢复默认的字段路径 */
    reset: string[];
    restart_required: boolean;
    config: PublicConfig;
}
