import { ref, watch, type Ref } from "vue";

const STORAGE_KEY = "soul-mate:local-settings";

export interface LocalSettings {
    ttsEnabled: boolean;
    ttsVolume: number;
}

const DEFAULTS: LocalSettings = {
    ttsEnabled: true,
    ttsVolume: 1.0,
};

export interface UseLocalSettingsResult {
    settings: Ref<LocalSettings>;
    update: (patch: Partial<LocalSettings>) => void;
    reset: () => void;
}

function load(): LocalSettings {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) {
            return { ...DEFAULTS };
        }
        const parsed = JSON.parse(raw);
        return {
            ttsEnabled:
                typeof parsed.ttsEnabled === "boolean" ? parsed.ttsEnabled : DEFAULTS.ttsEnabled,
            ttsVolume:
                typeof parsed.ttsVolume === "number"
                    ? Math.max(0, Math.min(1, parsed.ttsVolume))
                    : DEFAULTS.ttsVolume,
        };
    } catch {
        return { ...DEFAULTS };
    }
}

export function useLocalSettings(): UseLocalSettingsResult {
    const settings = ref<LocalSettings>(load());

    watch(
        settings,
        (val) => {
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify(val));
            } catch (err) {
                console.warn("[settings] localStorage 写入失败：", err);
            }
        },
        { deep: true },
    );

    function update(patch: Partial<LocalSettings>) {
        settings.value = { ...settings.value, ...patch };
    }

    function reset() {
        settings.value = { ...DEFAULTS };
    }

    return {
        settings,
        update,
        reset,
    };
}
