import { spawn, type ChildProcess } from "node:child_process";
import { EventEmitter } from "node:events";
import path from "node:path";

import { app } from "electron";

import type { PetSnapshot, PetStatus } from "./types.ts";

// 桌宠目录：dev 下就是 frontend/pet（和主应用同一个仓库）
const PET_DIR = path.resolve(app.getAppPath(), "pet");

// 等它自报 ready 的上限
const READY_TIMEOUT_MS = 8000;

const READY_MARK = "[pet] ready";
const ALREADY_MARK = "[pet] already-running";

type StartOutcome = "ready" | "already-running" | "exited" | "timeout";

/**
 * 桌宠管理器
 *
 * 形状照抄 BackendManager，但**语义相反：它是启动器，不是监护人**。
 * 桌宠是常驻的小家伙，关掉主应用不该把她一起带走，所以 spawn 用
 * detached + unref，退出流程里也故意不 stop()（见 main.ts 的注释）。
 *
 * 唯一性交给桌宠自己的单实例锁：重复启动时新进程会立刻退出并打印
 * "[pet] already-running"，这里据此把状态认成 running。
 * 代价是主应用重启之后不认得屏幕上已经存在的那一只——想彻底解决需要一个
 * 独立的探测通道（lock 文件或本地端口），见 pet/README.md 的待定项。
 */
export class PetManager extends EventEmitter {
    private proc: ChildProcess | null = null;
    private status: PetStatus = "stopped";
    private lastError: string | null = null;

    getSnapshot(): PetSnapshot {
        return { status: this.status, error: this.lastError };
    }

    private setStatus(status: PetStatus, error: string | null = null): void {
        this.status = status;
        this.lastError = error;
        console.log(`[pet] 状态：${status}${error === null ? "" : `（${error}）`}`);
        this.emit("status", this.getSnapshot());
    }

    /** 拉起桌宠；已经在跑（或屏幕上已经有一只）时返回 running */
    async start(): Promise<PetSnapshot> {
        if (this.proc !== null) {
            return this.getSnapshot();
        }

        if (app.isPackaged) {
            // 打包版还没有桌宠：它需要自己的一份 electron-builder 产物
            // （独立 appId / productName），再经 extraResources 放进 resources/pet
            this.setStatus("error", "打包版还没有集成桌宠");
            return this.getSnapshot();
        }

        this.setStatus("starting");

        // 用当前这个 electron 二进制跑桌宠目录，等价于 `electron frontend/pet`：
        // 版本天然一致，也不需要桌宠那边装依赖
        const child = spawn(process.execPath, [PET_DIR], {
            cwd: PET_DIR,
            detached: true,
            stdio: ["ignore", "pipe", "pipe"],
        });
        this.proc = child;
        child.unref();

        child.stdout?.on("data", (chunk: Buffer) => {
            process.stdout.write(`[pet] ${chunk.toString()}`);
        });
        child.stderr?.on("data", (chunk: Buffer) => {
            process.stderr.write(`[pet] ${chunk.toString()}`);
        });

        child.once("exit", (code) => {
            if (this.proc === child) {
                this.proc = null;
            }
            // 启动阶段的退出由 start() 自己收尾；这里只管"跑着跑着用户按了 Esc"
            if (this.status === "running") {
                console.log(`[pet] 已退出：code=${code}`);
                this.setStatus("stopped");
            }
        });

        const outcome = await this.waitForReady(child);
        if (outcome === "ready" || outcome === "already-running") {
            this.setStatus("running");
            return this.getSnapshot();
        }

        this.proc = null;
        this.setStatus(
            "error",
            outcome === "exited" ? "桌宠进程退出了（上面有它自己打的日志）" : "桌宠启动超时",
        );
        return this.getSnapshot();
    }

    /** 关掉桌宠；没在跑就当无事发生 */
    async stop(): Promise<PetSnapshot> {
        const child = this.proc;
        this.proc = null;

        if (child !== null && typeof child.pid === "number") {
            console.log(`[pet] 正在关闭：pid=${child.pid}`);
            if (process.platform === "win32") {
                // 桌宠下面还挂着 renderer / GPU 子进程，/T 不能省
                spawn("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
                    stdio: "ignore",
                });
            } else {
                try {
                    child.kill("SIGTERM");
                } catch {
                    // 已经没了
                }
            }
        }

        this.setStatus("stopped");
        return this.getSnapshot();
    }

    /** 等桌宠自报 ready / 说明自己已在跑 / 退出 / 超时 */
    private waitForReady(child: ChildProcess): Promise<StartOutcome> {
        return new Promise((resolve) => {
            let settled = false;

            const finish = (outcome: StartOutcome) => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                child.removeListener("exit", onExit);
                child.stdout?.off("data", onData);
                resolve(outcome);
            };

            const onExit = () => finish("exited");
            const onData = (chunk: Buffer) => {
                const text = chunk.toString();
                if (text.includes(READY_MARK)) {
                    finish("ready");
                } else if (text.includes(ALREADY_MARK)) {
                    finish("already-running");
                }
            };

            const timer = setTimeout(() => finish("timeout"), READY_TIMEOUT_MS);
            child.once("exit", onExit);
            // 转发日志的那两个监听在上面，这里只负责认握手
            child.stdout?.on("data", onData);
        });
    }
}
