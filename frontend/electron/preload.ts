import { contextBridge, ipcRenderer } from "electron";

import type { BackendSnapshot, PetSnapshot } from "./types.ts";

contextBridge.exposeInMainWorld("soulMate", {
    getBackendStatus: () => ipcRenderer.invoke("backend:get-status"),

    restartBackend: () => ipcRenderer.invoke("backend:restart"),

    onBackendStatus: (callback: (snapshot: BackendSnapshot) => void) => {
        const listener = (_event: unknown, snapshot: BackendSnapshot): void => {
            callback(snapshot);
        };
        ipcRenderer.on("backend:status", listener);
        return () => ipcRenderer.removeListener("backend:status", listener);
    },

    // 新增：打开系统文件选择对话框，返回选中的绝对路径；用户取消返回 null
    openFileDialog: (): Promise<string | null> => ipcRenderer.invoke("dialog:open-file"),

    // 桌宠：主应用是"启动器"，不是监护人（她可以比主应用活得久）
    getPetStatus: () => ipcRenderer.invoke("pet:get-status"),
    startPet: () => ipcRenderer.invoke("pet:start"),
    stopPet: () => ipcRenderer.invoke("pet:stop"),
    onPetStatus: (callback: (snapshot: PetSnapshot) => void) => {
        const listener = (_event: unknown, snapshot: PetSnapshot): void => {
            callback(snapshot);
        };
        ipcRenderer.on("pet:status", listener);
        return () => ipcRenderer.removeListener("pet:status", listener);
    },
});
