export class HttpError extends Error {
    status: number;

    constructor(status: number, detail: string) {
        super(detail);
        this.status = status;
        this.name = "HttpError";
    }
}

let baseUrl = "";

export function setBaseUrl(url: string) {
    baseUrl = url.replace(/\/$/, "");
}

/**
 * 单次请求的超时时间
 *
 * 之前没有超时：后端卡住（例如模型那边一直不吐字、数据库被锁）时，请求会一直
 * 挂着，界面既不报错也不恢复。20 秒对本地后端足够宽松。
 */
const REQUEST_TIMEOUT_MS = 20_000;

function describeNetworkError(err: unknown): string {
    if (err instanceof DOMException && err.name === "TimeoutError") {
        return `请求超时（${REQUEST_TIMEOUT_MS / 1000} 秒）`;
    }
    return err instanceof Error ? err.message : "网络请求失败";
}

/** 把 FastAPI 的错误体转成一句人话（detail 可能是字符串，也可能是校验错误数组） */
export function describeError(status: number, body: unknown): string {
    const detail = (body as { detail?: unknown } | null)?.detail;

    if (typeof detail === "string") {
        return detail;
    }

    if (Array.isArray(detail)) {
        const messages = detail
            .map((item) =>
                typeof item === "object" && item !== null && "msg" in item
                    ? String((item as { msg: unknown }).msg)
                    : null,
            )
            .filter((msg): msg is string => msg !== null);
        if (messages.length > 0) {
            return messages.join("；");
        }
    }

    return `HTTP ${status}`;
}

/**
 * 统一出口：带超时，非 2xx 一律转成带后端说明的 HttpError
 *
 * 接口地址写死在这里（而不是走 setBaseUrl）的调用方也能用，
 * 例如设置面板是拿"当前 host:port"现拼地址的。
 */
export async function fetchJson<T>(url: string, init: RequestInit = {}): Promise<T> {
    let res: Response;
    try {
        res = await fetch(url, {
            ...init,
            signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
        });
    } catch (err) {
        throw new HttpError(0, describeNetworkError(err));
    }

    if (!res.ok) {
        let body: unknown = null;
        try {
            body = await res.json();
        } catch {
            // 响应不是 JSON，用状态码兜底
        }
        throw new HttpError(res.status, describeError(res.status, body));
    }

    if (res.status === 204) {
        return undefined as T;
    }

    return (await res.json()) as T;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    if (!baseUrl) {
        throw new HttpError(0, "后端地址未设置");
    }

    const init: RequestInit = {
        method,
        headers: { "Content-Type": "application/json" },
    };
    if (body !== undefined) {
        init.body = JSON.stringify(body);
    }

    return fetchJson<T>(`${baseUrl}${path}`, init);
}

export const http = {
    get: <T>(path: string) => request<T>("GET", path),
    post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
    patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
    delete: <T>(path: string) => request<T>("DELETE", path),
};

export function isApiError(err: unknown): err is HttpError {
    return err instanceof HttpError;
}
