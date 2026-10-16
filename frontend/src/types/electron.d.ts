import type { BackendSnapshot, PetSnapshot, RestartResult } from "../../electron/types";

export type { BackendSnapshot, PetSnapshot, RestartResult };

/**
 * window.soulMate 的类型声明
 *
 * 类型源：electron/types.ts
 */
declare global {
    interface Window {
        soulMate: {
            getBackendStatus: () => Promise<BackendSnapshot>;
            restartBackend: () => Promise<RestartResult>;
            onBackendStatus: (callback: (snapshot: BackendSnapshot) => void) => () => void;

            // 新增：打开系统文件选择对话框，返回选中的绝对路径；用户取消返回 null
            openFileDialog: () => Promise<string | null>;

            // 桌宠
            getPetStatus: () => Promise<PetSnapshot>;
            startPet: () => Promise<PetSnapshot>;
            stopPet: () => Promise<PetSnapshot>;
            onPetStatus: (callback: (snapshot: PetSnapshot) => void) => () => void;
        };
    }
}

export {};
