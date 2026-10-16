/**
 * P3 spike：透明窗口 + WebGL + backdrop-filter 在 Windows 上的实际表现
 *
 * 目的不是"做个 3D demo"，而是把「透明无边框窗口 + WebGL canvas + 玻璃层」这套
 * 组合的真实行为测出来：有没有黑边 / 阴影异常 / 点击穿透准不准 / 置顶抢不抢焦点 /
 * 帧率与显存。结论写进 RESULTS.md，再决定 P3 怎么排期。
 *
 * 窗口几何：360×576 的小竖窗贴在主屏左侧靠下（工作区坐标，自动避开任务栏），
 * 和立绘 800×1279 同比。
 *
 * 用法：
 *   npm start                    目标配置：透明 + 无边框 + 玻璃 + WebGL
 *   npm run start:opaque         对照组：不透明（比黑边和阴影）
 *   npm run start:framed         对照组：带系统边框
 *   npm run start:plain          对照组：无玻璃、无 WebGL
 *   npm start -- --disable-gpu   对照组：软件渲染（比驱动相关的问题）
 *
 * 键盘（窗口级开关走键盘：开了点击穿透之后页面就点不到了）
 *   T 置顶    P 点击穿透    G 玻璃    W WebGL alpha    R 重载    Esc 退出
 */

const { app, BrowserWindow, ipcMain, screen } = require("electron");
const path = require("node:path");

// 父进程（主应用）退出后这条管道会断。不吞掉 EPIPE 的话，桌宠下一次
// console.log 就会把自己写崩——而它每次按键都会打日志，很容易撞上。
process.stdout.on("error", () => {});
process.stderr.on("error", () => {});

const argv = new Set(process.argv.slice(1));

// 单实例：桌宠同时只能有一只。主应用靠 already-running 这行判断"已经开着了"，
// 而不是自己另造一套探测。锁按 package.json 的 name（soul-mate-pet）分，
// 和主应用各用各的 userData，不会互相打架。
if (!app.requestSingleInstanceLock()) {
    // 必须同步写 + 立刻退出：console.log 是异步的，进程一退，没冲出去的握手行
    // 就丢了，主应用会一直等不到它。（没有 stdout 时 writeSync 会抛，忽略即可。）
    try {
        require("node:fs").writeSync(1, "[pet] already-running\n");
    } catch {
        // 双击启动之类没有 stdout 的场景，这里本来也没别的事要做
    }
    app.exit(0);
}

// 第二只被拒之后，用户再点一次图标就把已有那只拎到前面
app.on("second-instance", () => {
    if (win === null || win.isDestroyed()) return;
    if (win.isMinimized()) win.restore();
    win.show();
    win.focus();
});

// 默认就是「目标配置」；加 --no-xxx / --framed 换对照组。
// transparent 和 frame 只能在创建窗口时决定，所以对照组要重开一次进程。
const flags = {
    transparent: !argv.has("--no-transparent"),
    frameless: !argv.has("--framed"),
    glass: !argv.has("--no-glass"),
    webgl: !argv.has("--no-webgl"),
    onTop: !argv.has("--no-top"),
};

// 窗口几何：验证阶段写死，方便每次对比。正式版应该记住用户拖到的位置
// （架构文档里的"窗口尺寸 / 位置"）。
const WIN_WIDTH = 360;
const WIN_HEIGHT = 576; // 800×1279 的立绘比例
const WIN_MARGIN = 24;

/** @type {BrowserWindow | null} */
let win = null;
let clickThrough = false;

// Windows 上「透明」只在无边框窗口生效（Electron 文档：transparent "On Windows,
// does not work unless the window is frameless"）。所以 --framed 这条对照组会连
// 透明一起关掉——这里显式做并打日志，别让它静默失效（那会让人误判"边框不是原因"）。
const useTransparent = flags.transparent && flags.frameless;

function sendState() {
    if (win === null || win.isDestroyed()) return;
    win.webContents.send("spike:state", {
        clickThrough,
        alwaysOnTop: win.isAlwaysOnTop(),
    });
}

function createWindow() {
    // 贴主屏工作区的左侧靠下；workArea 已经扣掉任务栏
    const area = screen.getPrimaryDisplay().workArea;

    win = new BrowserWindow({
        width: WIN_WIDTH,
        height: WIN_HEIGHT,
        x: area.x + WIN_MARGIN,
        y: area.y + area.height - WIN_HEIGHT - WIN_MARGIN,
        show: false,
        transparent: useTransparent,
        frame: !flags.frameless,
        // 透明窗口别开系统阴影：Windows 上会沿矩形边界画出一圈方角
        hasShadow: false,
        backgroundColor: useTransparent ? "#00000000" : "#1a1a1f",
        alwaysOnTop: flags.onTop,
        webPreferences: {
            preload: path.join(__dirname, "preload.cjs"),
            contextIsolation: true,
            nodeIntegration: false,
        },
    });

    // 初始状态用 query 传给页面，省一次 IPC 往返
    win.loadFile(path.join(__dirname, "index.html"), {
        query: {
            transparent: String(useTransparent),
            glass: String(flags.glass),
            webgl: String(flags.webgl),
        },
    });

    // ready-to-show 正常会触发（paintWhenInitiallyHidden 默认 true），但透明窗口
    // 在个别驱动/版本上不触发，那就永远不显示——加个兜底，别让人以为"启动没反应"。
    let shown = false;
    const showOnce = () => {
        if (shown || win === null || win.isDestroyed()) return;
        shown = true;
        // 主应用按这行判断"起来了"（见 electron/pet.ts）
        console.log(`[pet] ready pid=${process.pid}`);
        win.show();
        sendState();
    };
    win.once("ready-to-show", showOnce);
    setTimeout(showOnce, 1200);

    win.webContents.on("before-input-event", (event, input) => {
        if (input.type !== "keyDown" || win === null || win.isDestroyed()) return;

        const key = String(input.key).toLowerCase();

        if (key === "t") {
            win.setAlwaysOnTop(!win.isAlwaysOnTop());
        } else if (key === "p") {
            clickThrough = !clickThrough;
            // forward: true 让鼠标事件继续透给下层窗口，页面仍收得到 mousemove
            win.setIgnoreMouseEvents(clickThrough, { forward: true });
        } else if (key === "g") {
            win.webContents.send("spike:toggle-glass");
        } else if (key === "w") {
            win.webContents.send("spike:toggle-webgl-alpha");
        } else if (key === "r") {
            win.reload();
        } else if (key === "escape") {
            app.quit();
        } else {
            return;
        }

        event.preventDefault();
        console.log(
            `[spike] ${key} → 置顶=${win.isAlwaysOnTop()} 穿透=${clickThrough}`,
        );
        sendState();
    });

    win.on("closed", () => {
        win = null;
    });
}

ipcMain.handle("spike:info", () => ({
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
    platform: `${process.platform} ${process.arch}`,
    flags,
    // 顺带把窗口的当前状态也回给页面：ready-to-show 时推的那一次有可能比渲染层
    // 注册 onState 更早（那样初值就丢了），页面自己拉一次就不依赖推送时序。
    state: {
        clickThrough,
        alwaysOnTop:
            win !== null && !win.isDestroyed() ? win.isAlwaysOnTop() : flags.onTop,
    },
}));

ipcMain.handle("spike:ignore-mouse", (_event, on) => {
    if (win === null || win.isDestroyed()) return false;
    clickThrough = Boolean(on);
    win.setIgnoreMouseEvents(clickThrough, { forward: true });
    sendState();
    return clickThrough;
});

ipcMain.handle("spike:always-on-top", (_event, on) => {
    if (win === null || win.isDestroyed()) return false;
    win.setAlwaysOnTop(Boolean(on));
    sendState();
    return win.isAlwaysOnTop();
});

app.whenReady().then(async () => {
    console.log(
        `[spike] electron=${process.versions.electron} chrome=${process.versions.chrome}`,
    );
    console.log(`[spike] flags=${JSON.stringify(flags)}`);
    console.log(
        `[spike] 实际生效的窗口配置：${JSON.stringify({
            size: `${WIN_WIDTH}x${WIN_HEIGHT}`,
            transparent: useTransparent,
            frame: !flags.frameless,
            hasShadow: false,
            alwaysOnTop: flags.onTop,
            clickThroughInApp: false,
        })}`,
    );
    if (flags.transparent && !flags.frameless) {
        console.warn(
            "[spike] 注意：--framed 在 Windows 上会连透明一起关掉（透明只在无边框窗口生效）",
        );
    }

    try {
        // 黑边 / 合成异常经常和显卡驱动有关，结论里要带上这台机器的 GPU 信息
        const gpu = await app.getGPUInfo("basic");
        console.log("[spike] GPU:", JSON.stringify(gpu));
    } catch (err) {
        console.warn("[spike] 取 GPU 信息失败：", err);
    }

    console.log("[spike] 窗口级快捷键：T 置顶 / P 穿透 / G 玻璃 / W alpha / R 重载 / Esc 退出");
    createWindow();
});

app.on("window-all-closed", () => {
    app.quit();
});
