export type BackendStatus = "starting" | "ready" | "stopped" | "error";

export interface BackendSnapshot {
    status: BackendStatus;
    error: string | null;
    host: string;
    port: number;
}

export interface RestartResult {
    ok: boolean;
    error?: string;
}

export type PetStatus = "starting" | "running" | "stopped" | "error";

export interface PetSnapshot {
    status: PetStatus;
    error: string | null;
}
