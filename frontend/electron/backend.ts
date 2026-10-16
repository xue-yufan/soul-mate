import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { EventEmitter } from "node:events";
import { app } from "electron";

import type { BackendSnapshot, BackendStatus } from "./types.ts";
import { loadUserConfig, prepareDataDir, userConfigToEnv, type UserConfig } from "./userConfig.ts";

// 后端目录：dev 下 frontend/ 的上一级是项目根，进 backend/；打包后从 resources 取
const BACKEND_DIR = app.isPackaged
    ? path.join(process.resourcesPath, "backend")
    : path.resolve(app.getAppPath(), "../backend");

const BACKEND_HOST = "127.0.0.1";
const BACKEND_PORT = 8000;
const HEALTH_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}/health`;

// /health 里用来认亲的字段：本项目的后端一定返回 app 名或 boot_id
const BACKEND_APP_NAME = "soul-mate-backend";

const POLL_INTERVAL_MS = 500;
const POLL_TIMEOUT_MS = 30000;
const STOP_TIMEOUT_MS = 10000;
const HEALTH_PROBE_TIMEOUT_MS = 1500;
// 打包版自带的 uv 与首次准备脚本（extraResources 放进 backend/）
const BUNDLED_UV = path.join(BACKEND_DIR, "uv.exe");
const SETUP_SCRIPT = path.join(BACKEND_DIR, "setup_env.cmd");
// 清理旧后端时，最多等它放开端口多久
const EVICT_WAIT_MS = 5000;
const EVICT_MAX_ATTEMPTS = 3;

interface BackendManagerOptions {
    isDev: boolean;
}

interface HealthBody {
    status?: string;
    app?: string;
    version?: string;
    pid?: number;
    boot_id?: string;
    uptime_s?: number;
}

interface HealthProbe {
    /** fetch 有没有拿到响应；false 表示端口上没人监听或连不上 */
    reachable: boolean;
    /** 响应体解析出的 JSON，解析失败为 null */
    body: HealthBody | null;
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

/** 这个响应体看起来是不是本项目的后端 */
function isOurBackend(body: HealthBody | null): boolean {
    if (!body) {
        return false;
    }
    return body.app === BACKEND_APP_NAME || typeof body.boot_id === "string";
}

export class BackendManager extends EventEmitter {
    private readonly isDev: boolean;
    private proc: ChildProcess | null = null;
    /** 是否是我们主动在停后端：用于区分「正常关闭」和「异常退出」 */
    private stopping = false;
    private status: BackendStatus = "stopped";
    private lastError: string | null = null;
    /** 首次准备环境的进程（打包版），退出时一并收掉 */
    private setupProc: ChildProcess | null = null;
    /** 用户配置（userData/settings.json），每次 start 时读一次 */
    private userConfig: UserConfig = {};
    /** 本次应用运行中自己拉起的那个后端的 boot_id：对不上就说明不是同一个实例 */
    private ownedBootId: string | null = null;

    constructor({ isDev }: BackendManagerOptions) {
        super();
        this.isDev = isDev;
    }

    /**
     * 启动后端
     *
     * 启动前先认亲：是自己的后端就复用，是本项目的旧后端就先清掉，
     * 是别的程序占着端口就报错，不再闷头复用。
     */
    async start(): Promise<void> {
        this.lastError = null;
        this.setStatus("starting");

        await this.reconcileExistingBackend();

        // 先读用户配置：这样首次运行时，配置模板在漫长的依赖下载**之前**
        // 就生成好了，用户可以先填 API Key
        this.userConfig = loadUserConfig();

        // 打包版：venv 还没准备好就先装（首次运行会弹控制台窗口，耗时几分钟）
        if (app.isPackaged) {
            await this.ensureBackendEnv();
        }

        try {
            this.spawnBackend();
        } catch (err) {
            this.lastError = err instanceof Error ? err.message : String(err);
            this.setStatus("error");
            throw err;
        }

        const health = await this.waitForHealth();
        if (!health) {
            this.lastError = "后端启动超时";
            this.setStatus("error");
            throw new Error("后端启动超时");
        }

        this.ownedBootId = health.boot_id ?? null;
        this.setStatus("ready");
    }

    /**
     * 停止后端
     *
     * Windows 用 taskkill /T /F 杀进程树
     * Unix 用 SIGTERM 触发后端优雅停机
     */
    async stop(): Promise<void> {
        // 首次准备环境还没跑完就退出：把那次 uv sync 一起收掉，别让它在后台继续下载
        if (this.setupProc !== null) {
            const setup = this.setupProc;
            this.setupProc = null;
            console.log("[backend] 正在结束环境准备进程...");
            try {
                this.taskKill(setup);
            } catch {
                // 忽略
            }
        }

        if (!this.proc) {
            this.ownedBootId = null;
            this.setStatus("stopped");
            return;
        }

        const proc = this.proc;
        this.stopping = true;
        console.log("[backend] 正在关闭...");

        return new Promise<void>((resolve) => {
            const timeout = setTimeout(() => {
                console.warn("[backend] 关闭超时，强制结束");
                this.forceKill(proc);
                resolve();
            }, STOP_TIMEOUT_MS);

            proc.once("exit", () => {
                clearTimeout(timeout);
                this.proc = null;
                this.stopping = false;
                this.lastError = null;
                this.ownedBootId = null;
                this.setStatus("stopped");
                resolve();
            });

            this.gracefulKill(proc);
        });
    }

    /**
     * 获取状态快照
     */
    getSnapshot(): BackendSnapshot {
        return {
            status: this.status,
            error: this.lastError,
            host: BACKEND_HOST,
            port: BACKEND_PORT,
        };
    }

    private setStatus(status: BackendStatus): void {
        if (this.status === status) {
            return;
        }
        this.status = status;
        console.log(`[backend] 状态：${status}`);
        this.emit("status", this.getSnapshot());
    }

    // ---------- Python 运行环境（打包版） ----------

    /** 应用私有的 venv：放 userData，避免往安装目录写（安装目录可能没有写权限） */
    private get managedVenvDir(): string {
        return path.join(app.getPath("userData"), "backend-venv");
    }

    /**
     * 找现成的解释器
     *
     * 优先应用私有 venv；其次安装目录旁边的 .venv（用户手动双击 setup_env.cmd 的情况，
     * 那时没有 UV_PROJECT_ENVIRONMENT，uv 会装到项目旁边）
     */
    private findVenvPython(): string | null {
        const candidates = [
            path.join(this.managedVenvDir, "Scripts", "python.exe"),
            path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe"),
        ];

        for (const candidate of candidates) {
            if (fs.existsSync(candidate)) {
                return candidate;
            }
        }
        return null;
    }

    /**
     * 首次运行准备 Python 环境
     *
     * 依赖要下几分钟到十几分钟，所以开一个**可见的控制台窗口**跑 setup_env.cmd：
     * 打包后的主进程没有控制台，自己打的日志用户看不到，那个窗口是唯一能看见
     * 进度和报错的地方。
     */
    private async ensureBackendEnv(): Promise<void> {
        if (this.findVenvPython() !== null) {
            return;
        }

        if (!fs.existsSync(BUNDLED_UV) || !fs.existsSync(SETUP_SCRIPT)) {
            throw this.failBackend(
                "安装包缺少 Python 运行环境（uv.exe / setup_env.cmd），请重新安装完整版本",
            );
        }

        console.log("[backend] 首次运行：准备 Python 环境，会弹出一个控制台窗口");

        await new Promise<void>((resolve, reject) => {
            // 注意：start 打开 .cmd 默认用 cmd /K（跑完不关窗口，/wait 会永远等），
            // 所以显式包一层 cmd /c；另外 start /wait 不传回脚本退出码，
            // 成败一律以「venv 里的解释器能不能用」为准
            const child = spawn(`start "Soul Mate 环境准备" /wait cmd /c "${SETUP_SCRIPT}"`, {
                cwd: BACKEND_DIR,
                env: {
                    ...process.env,
                    // 装到应用私有目录，不写安装目录
                    UV_PROJECT_ENVIRONMENT: this.managedVenvDir,
                    // uv 自己下载的 Python 也放用户目录，省得没权限或占安装盘
                    UV_PYTHON_INSTALL_DIR: path.join(app.getPath("userData"), "python"),
                },
                shell: true,
                stdio: "ignore",
            });

            this.setupProc = child;

            child.on("error", (err: Error) => {
                this.setupProc = null;
                reject(this.failBackend(`无法启动环境准备：${err.message}`));
            });

            child.on("exit", () => {
                this.setupProc = null;

                const python = this.findVenvPython();
                if (python !== null && this.isEnvUsable(python)) {
                    console.log("[backend] Python 环境准备完成");
                    resolve();
                    return;
                }
                reject(this.failBackend("Python 环境准备失败，请看刚才那个控制台窗口里的错误信息"));
            });
        });
    }

    /** venv 里的解释器能不能真的导入后端依赖（venv 建了一半也会留下 python.exe） */
    private isEnvUsable(python: string): boolean {
        try {
            const result = spawnSync(python, ["-c", "import uvicorn, fastapi, pydantic"], {
                stdio: "ignore",
                timeout: 30000,
            });
            return result.status === 0;
        } catch {
            return false;
        }
    }

    private spawnBackend(): void {
        console.log(`[backend] 启动：cwd=${BACKEND_DIR}`);

        // 打包版直接跑 venv 里的解释器，不再经过 uv：启动快，也不用联网
        const venvPython = app.isPackaged ? this.findVenvPython() : null;
        const command = venvPython ?? "uv";
        const args =
            venvPython !== null
                ? [
                      "-m",
                      "uvicorn",
                      "app.main:app",
                      "--host",
                      BACKEND_HOST,
                      "--port",
                      String(BACKEND_PORT),
                  ]
                : [
                      "run",
                      "uvicorn",
                      "app.main:app",
                      "--host",
                      BACKEND_HOST,
                      "--port",
                      String(BACKEND_PORT),
                  ];
        console.log(`[backend] 解释器：${venvPython ?? "uv（开发模式）"}`);

        // 使用者的 API Key / 功能开关 / 数据目录都从 userData 读，翻译成环境变量下发
        const userConfig = this.userConfig;
        const dataDir = prepareDataDir(userConfig);
        if (dataDir !== null) {
            console.log(`[backend] 数据目录：${dataDir}`);
        }
        // settings.json 只在打包版生效：开发时以 backend/config.local.yaml 为准，
        // 否则模板里的 features（memory/rag=false）会把开发环境的记忆和知识库关掉
        const userEnv = app.isPackaged ? userConfigToEnv(userConfig) : {};
        if (!app.isPackaged) {
            console.log("[config] 开发模式：settings.json 不生效，沿用 backend/config.local.yaml");
        }

        this.stopping = false;

        // 打包版走的是绝对路径（userData 里的 venv），而 userData 目录名是 productName
        // （"%APPDATA%\Soul Mate"），**路径里带空格**。
        // shell: true 时 Node 只是把 command 和 args 拼成字符串丢给 cmd，
        // 不会给 command 加引号，于是 cmd 把 "C:\Users\..." 的第一段当成命令名：
        //   'C:\Users\...' is not recognized as an internal or external command
        // 实测：spawn('C:\\Program Files\\...\\node.exe', [...], { shell: true }) 必失败。
        // 所以只有走 uv（需要 shell 去查 PATH）时才开 shell，绝对路径一律直接 spawn。
        const useShell = venvPython === null;

        this.proc = spawn(command, args, {
            cwd: BACKEND_DIR,
            env: {
                ...process.env,
                SOUL_MATE_APP__ENV: this.isDev ? "dev" : "prod",
                PYTHONUNBUFFERED: "1",
                // Windows 下 Python 输出到管道时默认用系统区域编码（中文环境是 GBK），
                // 这里固定成 UTF-8，才能和下面的 data.toString() 对上
                PYTHONIOENCODING: "utf-8",
                // 后端首次对话会懒加载本地嵌入模型，transformers 的进度条往管道写
                // 控制字符时会抛 OSError [Errno 22]，把记忆召回连带整个请求搞挂，
                // 这里直接关掉进度条（transformers 的开关就挂在 huggingface_hub 上）
                HF_HUB_DISABLE_PROGRESS_BARS: "1",
                // 后端据此判断父进程（本进程）是否还活着：父进程一没就自己退出，
                // 覆盖崩溃 / 被强杀 / 热重启这些走不到 before-quit 的情况
                SOUL_MATE_PARENT_PID: String(process.pid),
                // 数据（SQLite / 日志）落到 userData，不写安装目录；开发版不设，沿用 ./data
                ...(dataDir !== null ? { SOUL_MATE_PATHS__DATA_DIR: dataDir } : {}),
                // 使用者的模型配置与功能开关
                ...userEnv,
            },
            shell: useShell,
        });

        this.proc.stdout?.on("data", (data: Buffer) => {
            process.stdout.write(`[backend] ${data.toString()}`);
        });

        this.proc.stderr?.on("data", (data: Buffer) => {
            process.stderr.write(`[backend] ${data.toString()}`);
        });

        this.proc.on("error", (err: Error) => {
            console.error("[backend] 进程错误：", err);
            this.lastError = err.message;
            this.setStatus("error");
        });

        this.proc.on("exit", (code, signal) => {
            console.log(`[backend] 进程退出：code=${code} signal=${signal}`);
            this.proc = null;
            if (this.stopping) {
                // 主动 stop() 触发的退出，交给 stop() 收尾，不算异常
                return;
            }
            if (this.status !== "stopped") {
                this.lastError = `后端异常退出：code=${code}`;
                this.setStatus("error");
            }
        });
    }

    /**
     * 探测 /health
     *
     * reachable 只表示「端口上有人应答」，即使 HTTP 状态码不是 200 也算，
     * 这样才能把「旧后端在跑」和「端口是空的」区分开。
     */
    private async probeHealth(timeoutMs: number = HEALTH_PROBE_TIMEOUT_MS): Promise<HealthProbe> {
        try {
            const res = await fetch(HEALTH_URL, {
                signal: AbortSignal.timeout(timeoutMs),
            });

            let body: HealthBody | null = null;
            try {
                body = (await res.json()) as HealthBody;
            } catch {
                body = null;
            }

            return { reachable: true, body };
        } catch {
            return { reachable: false, body: null };
        }
    }

    /**
     * 启动前认亲：8000 上已有的后端是谁
     *
     * - 自己这次运行拉起的（boot_id 相同）→ 复用
     * - 本项目的旧后端（代码可能已经过期）→ 先结束它，避免前端连到旧代码
     * - 别的程序占着端口 → 报错，不动它
     */
    private async reconcileExistingBackend(): Promise<void> {
        const probe = await this.probeHealth();
        if (!probe.reachable) {
            return;
        }

        const body = probe.body;

        if (body && this.ownedBootId !== null && body.boot_id === this.ownedBootId) {
            console.log("[backend] 检测到本次运行拉起的后端，跳过 spawn");
            return;
        }

        if (isOurBackend(body)) {
            await this.evictStaleBackend(body!);
            return;
        }

        const detail = body ? `app=${body.app ?? "?"}` : "响应不是 JSON";
        throw this.failBackend(`端口 ${BACKEND_PORT} 已被其他程序占用（${detail}）`);
    }

    /**
     * 结束端口上的旧后端
     *
     * 用 /health 上报的 pid 杀整棵进程树。如果杀完又冒出一个新 pid
     * （uvicorn --reload 的 supervisor 会重启 worker），重试几次仍不行就报错，
     * 交给用户手动处理，不做无上限的追打。
     */
    private async evictStaleBackend(body: HealthBody): Promise<void> {
        let current = body;

        for (let attempt = 1; attempt <= EVICT_MAX_ATTEMPTS; attempt += 1) {
            const pid = current.pid;

            if (typeof pid !== "number" || pid <= 0) {
                throw this.failBackend(
                    `端口 ${BACKEND_PORT} 上已有旧后端，但它没有上报 pid，请手动关闭后重试`,
                );
            }

            console.warn(
                `[backend] 端口 ${BACKEND_PORT} 上是旧后端（pid=${pid} boot_id=${current.boot_id ?? "?"}），正在结束它（第 ${attempt} 次）`,
            );
            this.killTreeByPid(pid);

            if (await this.waitUntilPortFree(EVICT_WAIT_MS)) {
                console.log("[backend] 旧后端已结束，端口已释放");
                return;
            }

            const probe = await this.probeHealth();
            if (!probe.reachable) {
                return;
            }

            if (!isOurBackend(probe.body) || probe.body?.boot_id === current.boot_id) {
                throw this.failBackend(`端口 ${BACKEND_PORT} 已被其他程序占用，请先释放该端口`);
            }

            // 换了个新实例（例如 --reload 的 supervisor 又拉了一个），再来一轮
            current = probe.body!;
        }

        throw this.failBackend(
            `端口 ${BACKEND_PORT} 上的旧后端关不掉（可能是 scripts/run_dev.ps1 用 --reload 启动的），请手动关闭后重试`,
        );
    }

    /** 等到端口上没人应答为止 */
    private async waitUntilPortFree(timeoutMs: number): Promise<boolean> {
        const deadline = Date.now() + timeoutMs;
        while (Date.now() < deadline) {
            const probe = await this.probeHealth(800);
            if (!probe.reachable) {
                return true;
            }
            await sleep(200);
        }
        return false;
    }

    private failBackend(message: string): Error {
        this.lastError = message;
        this.setStatus("error");
        return new Error(message);
    }

    /** 按 pid 杀整棵进程树（后端是 uv → uvicorn → worker 的链） */
    private killTreeByPid(pid: number): void {
        if (process.platform === "win32") {
            try {
                spawn("taskkill", ["/PID", String(pid), "/T", "/F"], {
                    stdio: "ignore",
                });
            } catch {
                // 忽略
            }
            return;
        }

        try {
            process.kill(pid, "SIGTERM");
        } catch {
            // 忽略
        }
        setTimeout(() => {
            try {
                process.kill(pid, "SIGKILL");
            } catch {
                // 忽略
            }
        }, 2000);
    }

    private async waitForHealth(): Promise<HealthBody | null> {
        const start = Date.now();
        while (Date.now() - start < POLL_TIMEOUT_MS) {
            if (this.proc === null) {
                return null;
            }
            const probe = await this.probeHealth(2000);
            if (probe.reachable && probe.body?.status === "ok") {
                return probe.body;
            }
            await sleep(POLL_INTERVAL_MS);
        }
        return null;
    }

    private gracefulKill(proc: ChildProcess): void {
        if (process.platform === "win32") {
            this.taskKill(proc);
        } else {
            proc.kill("SIGTERM");
        }
    }

    private forceKill(proc: ChildProcess): void {
        if (process.platform === "win32") {
            this.taskKill(proc);
        } else {
            proc.kill("SIGKILL");
        }
    }

    private taskKill(proc: ChildProcess): void {
        if (!proc.pid) {
            try {
                proc.kill();
            } catch {
                // 忽略
            }
            return;
        }

        try {
            spawn("taskkill", ["/PID", String(proc.pid), "/T", "/F"], {
                stdio: "ignore",
            });
        } catch {
            try {
                proc.kill();
            } catch {
                // 忽略
            }
        }
    }
}
