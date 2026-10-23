import { computed, onUnmounted, readonly, ref, watch, type ComputedRef } from "vue";

import type { CameraCaptureRequestEvent, CameraCapability } from "@/types/protocol";
import type { UseWebSocketResult } from "./useWebSocket";

const MAX_IMAGE_EDGE = 1024;
const JPEG_QUALITY = 0.75;
const CAMERA_READY_TIMEOUT_MS = 8_000;

export interface UseVisionCameraResult {
    enabled: Readonly<ReturnType<typeof ref<boolean>>>;
    supported: Readonly<ReturnType<typeof ref<boolean>>>;
    capturing: Readonly<ReturnType<typeof ref<boolean>>>;
    error: Readonly<ReturnType<typeof ref<string | null>>>;
    statusText: ComputedRef<string>;
    toggle: () => Promise<void>;
}

function blobToBase64(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = () => reject(new Error("读取摄像头图片失败"));
        reader.onload = () => {
            const result = reader.result;
            if (typeof result !== "string") {
                reject(new Error("摄像头图片编码失败"));
                return;
            }
            const comma = result.indexOf(",");
            resolve(comma >= 0 ? result.slice(comma + 1) : result);
        };
        reader.readAsDataURL(blob);
    });
}

function waitForVideo(video: HTMLVideoElement): Promise<void> {
    if (video.readyState >= HTMLMediaElement.HAVE_METADATA && video.videoWidth > 0) {
        return Promise.resolve();
    }

    return new Promise((resolve, reject) => {
        const timer = window.setTimeout(() => {
            cleanup();
            reject(new Error("摄像头启动超时"));
        }, CAMERA_READY_TIMEOUT_MS);

        function cleanup() {
            window.clearTimeout(timer);
            video.removeEventListener("loadedmetadata", onLoaded);
            video.removeEventListener("error", onError);
        }

        function onLoaded() {
            cleanup();
            resolve();
        }

        function onError() {
            cleanup();
            reject(new Error("无法读取摄像头画面"));
        }

        video.addEventListener("loadedmetadata", onLoaded, { once: true });
        video.addEventListener("error", onError, { once: true });
    });
}

export function useVisionCamera(ws: UseWebSocketResult): UseVisionCameraResult {
    const supported = ref(
        typeof navigator !== "undefined" &&
            typeof navigator.mediaDevices?.getUserMedia === "function",
    );
    const enabled = ref(false);
    const capturing = ref(false);
    const error = ref<string | null>(null);

    let stream: MediaStream | null = null;
    let video: HTMLVideoElement | null = null;
    let enabling = false;

    const statusText = computed(() => {
        if (!supported.value) return "摄像头不可用";
        if (capturing.value) return "正在看一眼";
        if (enabled.value) return "视觉已开启";
        return "视觉关闭";
    });

    function announceCapabilities(capabilities: CameraCapability[]) {
        if (ws.status.value !== "connected") return;
        ws.send({
            type: "client_hello",
            request_id: `camera-${Date.now()}`,
            data: { capabilities },
        });
    }

    function stopTracks() {
        stream?.getTracks().forEach((track) => track.stop());
        stream = null;
        if (video) {
            video.srcObject = null;
        }
    }

    function disable(notifyBackend = true) {
        stopTracks();
        enabled.value = false;
        capturing.value = false;
        if (notifyBackend) {
            announceCapabilities([]);
        }
    }

    async function enable(): Promise<boolean> {
        if (enabled.value) return true;
        if (enabling) return false;
        if (!supported.value) {
            error.value = "当前系统没有可用的摄像头接口";
            return false;
        }
        if (ws.status.value !== "connected") {
            error.value = "后端尚未连接，暂时不能开启视觉";
            return false;
        }

        enabling = true;
        error.value = null;
        try {
            const nextStream = await navigator.mediaDevices.getUserMedia({
                audio: false,
                video: {
                    facingMode: "user",
                    width: { ideal: 1280 },
                    height: { ideal: 720 },
                },
            });

            const nextVideo = document.createElement("video");
            nextVideo.autoplay = true;
            nextVideo.muted = true;
            nextVideo.playsInline = true;
            nextVideo.srcObject = nextStream;

            try {
                await waitForVideo(nextVideo);
                await nextVideo.play();
            } catch (err) {
                nextStream.getTracks().forEach((track) => track.stop());
                throw err;
            }

            stream = nextStream;
            video = nextVideo;
            enabled.value = true;
            announceCapabilities(["camera"]);
            return true;
        } catch (err) {
            stopTracks();
            enabled.value = false;
            const detail = err instanceof Error ? err.message : String(err);
            error.value =
                detail.includes("Permission") || detail.includes("NotAllowed")
                    ? "摄像头权限被拒绝，请在系统设置中允许访问"
                    : `摄像头启动失败：${detail}`;
            return false;
        } finally {
            enabling = false;
        }
    }

    async function toggle(): Promise<void> {
        if (enabled.value) {
            disable();
            return;
        }
        const ok = await enable();
        if (!ok && error.value) {
            window.alert(error.value);
        }
    }

    async function captureFrame(): Promise<{ image: string; mimeType: string }> {
        if (!enabled.value || !stream || !video) {
            throw new Error("用户未开启摄像头");
        }

        await waitForVideo(video);
        const sourceWidth = video.videoWidth;
        const sourceHeight = video.videoHeight;
        if (sourceWidth <= 0 || sourceHeight <= 0) {
            throw new Error("摄像头没有产生有效画面");
        }

        const scale = Math.min(1, MAX_IMAGE_EDGE / Math.max(sourceWidth, sourceHeight));
        const width = Math.max(1, Math.round(sourceWidth * scale));
        const height = Math.max(1, Math.round(sourceHeight * scale));
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;

        const context = canvas.getContext("2d");
        if (!context) {
            throw new Error("无法创建图片画布");
        }
        context.drawImage(video, 0, 0, width, height);

        const blob = await new Promise<Blob>((resolve, reject) => {
            canvas.toBlob(
                (value) => {
                    if (value) {
                        resolve(value);
                    } else {
                        reject(new Error("摄像头画面压缩失败"));
                    }
                },
                "image/jpeg",
                JPEG_QUALITY,
            );
        });

        return {
            image: await blobToBase64(blob),
            mimeType: "image/jpeg",
        };
    }

    async function onCaptureRequest(event: CameraCaptureRequestEvent) {
        const { capture_id: captureId } = event.data;
        if (!enabled.value) {
            ws.send({
                type: "camera_capture_result",
                request_id: captureId,
                data: {
                    capture_id: captureId,
                    ok: false,
                    error: "用户未开启摄像头",
                },
            });
            return;
        }

        if (capturing.value) {
            ws.send({
                type: "camera_capture_result",
                request_id: captureId,
                data: {
                    capture_id: captureId,
                    ok: false,
                    error: "上一帧仍在处理",
                },
            });
            return;
        }

        capturing.value = true;
        error.value = null;
        try {
            const frame = await captureFrame();
            ws.send({
                type: "camera_capture_result",
                request_id: captureId,
                data: {
                    capture_id: captureId,
                    ok: true,
                    image: frame.image,
                    mime_type: frame.mimeType,
                },
            });
        } catch (err) {
            const detail = err instanceof Error ? err.message : String(err);
            error.value = detail;
            ws.send({
                type: "camera_capture_result",
                request_id: captureId,
                data: {
                    capture_id: captureId,
                    ok: false,
                    error: detail,
                },
            });
        } finally {
            capturing.value = false;
        }
    }

    const unsubscribeCapture = ws.on("camera_capture_request", (event) => {
        if (event.type === "camera_capture_request") {
            void onCaptureRequest(event);
        }
    });

    watch(ws.status, (status) => {
        if (status === "connected" && enabled.value) {
            announceCapabilities(["camera"]);
        } else if (status !== "connected" && enabled.value) {
            // 后端断开后不继续占用摄像头；重新连接需要用户再次开启。
            disable(false);
        }
    });

    onUnmounted(() => {
        unsubscribeCapture();
        disable();
    });

    return {
        enabled: readonly(enabled),
        supported: readonly(supported),
        capturing: readonly(capturing),
        error: readonly(error),
        statusText,
        toggle,
    };
}
