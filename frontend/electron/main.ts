import { app, BrowserWindow, dialog, ipcMain, shell, type OpenDialogOptions } from "electron";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { BackendManager } from "./backend.ts";
import { PetManager } from "./pet.ts";
import type { BackendSnapshot, PetSnapshot, RestartResult } from "./types.ts";

// 不用声明 __dirname：electron-vite 在 ESM 产物里会注入同名垫片，自己再声明会重复定义
const moduleDir = path.dirname(fileURLToPath(import.meta.url));
const isDev = !app.isPackaged;

/**
 * 窗口/任务栏图标。
 *
 * 打包版由 exe 自带的图标负责（electron-builder 从 build/icon.ico 打进去），
 * dev 下 exe 是 electron 自己的，所以这里显式指一下，免得调试时任务栏是 electron 的 logo。
 */
const windowIcon = path.join(moduleDir, "../../build/icon.ico");

let mainWindow: BrowserWindow | null = null;
let backend: BackendManager | null = null;
let pet: PetManager | null = null;

/**
 * 外链交给系统浏览器，页面本身不跳转
 *
 * 模型回复里的 Markdown 链接（渲染成 `<a href="https://…">`）如果直接导航，
 * 整个应用窗口会变成那个网页：没有后退键、没有菜单，用户只能重启应用。
 */
function installLinkGuard(win: BrowserWindow, devUrl: string | undefined): void {
    // 打包版是 file:// 页面（origin 是字符串 "null"），页面内不存在合法跳转；
    // 开发版放行 dev server 的同源导航（HMR 刷新、location.reload）
    let selfOrigin = "null";
    if (devUrl) {
        try {
            selfOrigin = new URL(devUrl).origin;
        } catch {
            selfOrigin = "null";
        }
    }

    const openInSystemBrowser = (url: string): void => {
        // 不写 = ""：catch 里直接 return，这个初值永远不会被读到
        let protocol: string;
        try {
            protocol = new URL(url).protocol;
        } catch {
            return;
        }
        if (protocol !== "http:" && protocol !== "https:" && protocol !== "mailto:") {
            return;
        }
        void shell.openExternal(url);
    };

    // window.open / target=_blank：不开新窗口
    win.webContents.setWindowOpenHandler(({ url }) => {
        openInSystemBrowser(url);
        return { action: "deny" };
    });

    win.webContents.on("will-navigate", (event, url) => {
        if (selfOrigin !== "null" && isSameOrigin(url, selfOrigin)) {
            return;
        }
        event.preventDefault();
        openInSystemBrowser(url);
    });
}

function isSameOrigin(url: string, origin: string): boolean {
    try {
        return new URL(url).origin === origin;
    } catch {
        return false;
    }
}

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1200,
        height: 800,
        minWidth: 900,
        minHeight: 600,
        title: "Soul Mate",
        icon: fs.existsSync(windowIcon) ? windowIcon : undefined,
        backgroundColor: "#1a1a1f",
        webPreferences: {
            preload: path.join(moduleDir, "../preload/index.cjs"),
            contextIsolation: true,
            nodeIntegration: false,
        },
    });

    // dev 下由 electron-vite 起渲染层的 dev server，地址通过环境变量给出
    const rendererUrl = process.env["ELECTRON_RENDERER_URL"];

    if (isDev && rendererUrl) {
        mainWindow.loadURL(rendererUrl);
        mainWindow.webContents.openDevTools({ mode: "right" });
    } else {
        mainWindow.loadFile(path.join(moduleDir, "../renderer/index.html"));
    }

    installLinkGuard(mainWindow, isDev ? rendererUrl : undefined);

    mainWindow.on("closed", () => {
        mainWindow = null;
    });
}

function setupBackend(): void {
    backend = new BackendManager({ isDev });

    // 状态变化推送到渲染进程
    backend.on("status", (snapshot: BackendSnapshot) => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send("backend:status", snapshot);
        }
    });

    // 渲染进程主动查询
    ipcMain.handle("backend:get-status", (): BackendSnapshot => {
        if (!backend) {
            return {
                status: "stopped",
                error: "后端管理器未初始化",
                host: "127.0.0.1",
                port: 8000,
            };
        }
        return backend.getSnapshot();
    });

    // 渲染进程请求重启
    ipcMain.handle("backend:restart", async (): Promise<RestartResult> => {
        if (!backend) {
            return { ok: false, error: "后端管理器未初始化" };
        }
        try {
            await backend.stop();
            await backend.start();
            return { ok: true };
        } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            return { ok: false, error: message };
        }
    });

    // 异步启动，不阻塞窗口显示
    backend.start().catch((err: unknown) => {
        console.error("[main] 后端启动失败：", err);
    });
}

/**
 * 桌宠：主进程只负责"拉起 / 关掉 / 报状态"，不接管它的生命周期
 *
 * 它和 backend 的语义是反的：后端是附属品（主应用退出就一起收），桌宠是常驻的
 * 小家伙，spawn 时 detached，退出时也不杀。要改这个策略，先想清楚"主应用重启后
 * 要不要认得屏幕上那一只"——见 electron/pet.ts 的注释。
 */
function setupPet(): void {
    pet = new PetManager();

    pet.on("status", (snapshot: PetSnapshot) => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send("pet:status", snapshot);
        }
    });

    ipcMain.handle("pet:get-status", (): PetSnapshot => {
        if (!pet) {
            return { status: "stopped", error: "桌宠管理器未初始化" };
        }
        return pet.getSnapshot();
    });

    ipcMain.handle("pet:start", async (): Promise<PetSnapshot> => {
        if (!pet) {
            return { status: "error", error: "桌宠管理器未初始化" };
        }
        return pet.start();
    });

    ipcMain.handle("pet:stop", async (): Promise<PetSnapshot> => {
        if (!pet) {
            return { status: "error", error: "桌宠管理器未初始化" };
        }
        return pet.stop();
    });
}

/**
 * 注册系统级对话框的 IPC handler。
 *
 * 目前只有"选择本地文件"一个用途：前端从文件导入知识库文档时，
 * 由主进程弹系统文件选择框，拿到绝对路径后回给渲染进程，
 * 再由渲染进程把路径发给后端读文件——渲染进程本身没有文件系统权限。
 */
function setupDialogs(): void {
    const options: OpenDialogOptions = {
        title: "选择要导入的文档",
        properties: ["openFile"],
        filters: [
            {
                name: "文本文件",
                extensions: ["txt", "md", "markdown", "json"],
            },
            { name: "Word 文档", extensions: ["docx"] },
            { name: "所有文件", extensions: ["*"] },
        ],
    };

    ipcMain.handle("dialog:open-file", async (): Promise<string | null> => {
        const win = mainWindow && !mainWindow.isDestroyed() ? mainWindow : null;
        const result = win
            ? await dialog.showOpenDialog(win, options)
            : await dialog.showOpenDialog(options);

        if (result.canceled || result.filePaths.length === 0) {
            return null;
        }
        return result.filePaths[0];
    });
}

// 单实例：开出第二个应用会各拉一个后端、互相抢 8000，这里直接让后来的退出
const hasSingleInstanceLock = app.requestSingleInstanceLock();

if (!hasSingleInstanceLock) {
    app.quit();
} else {
    // 第二个实例被拒后，用户再点一次图标时把已有窗口拎到前面
    app.on("second-instance", () => {
        if (!mainWindow || mainWindow.isDestroyed()) {
            return;
        }
        if (mainWindow.isMinimized()) {
            mainWindow.restore();
        }
        mainWindow.focus();
    });

    app.whenReady().then(() => {
        setupDialogs();
        setupBackend();
        setupPet();
        createWindow();

        app.on("activate", () => {
            if (BrowserWindow.getAllWindows().length === 0) {
                createWindow();
            }
        });
    });
}

app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
        app.quit();
    }
});

// 退出前停后端
//
// 桌宠故意不在这里停：她是常驻的小家伙，关掉主应用不该把她一起带走
// （PetManager spawn 时用了 detached，她本来就活着）。要改这个策略，先想清楚
// "主应用重启后怎么认得屏幕上那一只"——见 electron/pet.ts。
let quitting = false;
app.on("before-quit", (event) => {
    if (quitting || !backend) {
        return;
    }

    event.preventDefault();
    quitting = true;

    backend
        .stop()
        .catch((err: unknown) => {
            console.error("[main] 关闭后端失败：", err);
        })
        .finally(() => {
            app.quit();
        });
});
