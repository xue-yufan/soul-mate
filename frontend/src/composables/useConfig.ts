import { ref, type Ref } from "vue";

import { fetchJson } from "@/api/http";
import type { ConfigPatchResult, PublicConfig } from "@/types/config";

export interface UseConfigResult {
    config: Ref<PublicConfig | null>;
    loading: Ref<boolean>;
    error: Ref<string | null>;
    refresh: (baseUrl: string) => Promise<void>;
    save: (
        baseUrl: string,
        values: Record<string, unknown>,
        reset?: string[],
    ) => Promise<ConfigPatchResult>;
}

export function useConfig(): UseConfigResult {
    const config = ref<PublicConfig | null>(null);
    const loading = ref(false);
    const error = ref<string | null>(null);

    async function refresh(baseUrl: string): Promise<void> {
        loading.value = true;
        error.value = null;

        try {
            config.value = await fetchJson<PublicConfig>(`${baseUrl}/config/public`);
        } catch (err) {
            error.value = err instanceof Error ? err.message : String(err);
            config.value = null;
        } finally {
            loading.value = false;
        }
    }

    /**
     * 写入白名单内的配置项（后端只接受白名单里的路径，且拒绝被环境变量固定的项）
     *
     * 成功时用响应里带的快照刷新 config；失败时抛出带后端说明的错误，
     * 由调用方决定怎么展示。
     */
    async function save(
        baseUrl: string,
        values: Record<string, unknown>,
        reset: string[] = [],
    ): Promise<ConfigPatchResult> {
        try {
            const result = await fetchJson<ConfigPatchResult>(`${baseUrl}/config`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ values, reset }),
            });
            config.value = result.config;
            error.value = null;
            return result;
        } catch (err) {
            // 失败时抛出带后端说明的错误（HttpError 的 message 就是说明），
            // 由调用方决定怎么展示
            error.value = err instanceof Error ? err.message : String(err);
            throw err;
        }
    }

    return {
        config,
        loading,
        error,
        refresh,
        save,
    };
}
