import { defineComponent, h } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useVisionCamera, type UseVisionCameraResult } from "@/composables/useVisionCamera";
import { createFakeWebSocket, serverEvent } from "@/test/helpers";

class FakeFileReader {
    result: string | null = null;
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;

    readAsDataURL() {
        this.result = "data:image/jpeg;base64,ZmFrZS1qcGVn";
        this.onload?.();
    }
}

function installCameraDomMocks() {
    const stopTrack = vi.fn();
    const stream = {
        getTracks: () => [{ stop: stopTrack }],
    } as unknown as MediaStream;
    const getUserMedia = vi.fn(async () => stream);
    const originalMediaDevices = Object.getOwnPropertyDescriptor(navigator, "mediaDevices");

    Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: { getUserMedia },
    });

    const drawImage = vi.fn();
    const canvas = {
        width: 0,
        height: 0,
        getContext: vi.fn(() => ({ drawImage })),
        toBlob: vi.fn((callback: BlobCallback) => {
            callback(new Blob(["fake-jpeg"], { type: "image/jpeg" }));
        }),
    };
    const video = {
        autoplay: false,
        muted: false,
        playsInline: false,
        readyState: 2,
        videoWidth: 1920,
        videoHeight: 1080,
        srcObject: null,
        play: vi.fn(async () => {}),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
    };

    vi.stubGlobal("FileReader", FakeFileReader);
    const alert = vi.fn();
    vi.stubGlobal("alert", alert);
    const originalCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation(((
        tagName: string,
        options?: ElementCreationOptions,
    ) => {
        if (tagName === "video") return video as unknown as HTMLElement;
        if (tagName === "canvas") return canvas as unknown as HTMLElement;
        return originalCreateElement(tagName, options);
    }) as typeof document.createElement);

    return {
        getUserMedia,
        stopTrack,
        canvas,
        drawImage,
        alert,
        restoreMediaDevices() {
            if (originalMediaDevices) {
                Object.defineProperty(navigator, "mediaDevices", originalMediaDevices);
            } else {
                Reflect.deleteProperty(navigator, "mediaDevices");
            }
        },
    };
}

let activeCamera: UseVisionCameraResult | null = null;
let restoreMediaDevices: (() => void) | null = null;

function mountCamera(ws: ReturnType<typeof createFakeWebSocket>) {
    const Host = defineComponent({
        setup() {
            activeCamera = useVisionCamera(ws.result);
            return () => h("div");
        },
    });
    return mount(Host);
}

afterEach(() => {
    activeCamera = null;
    restoreMediaDevices?.();
    restoreMediaDevices = null;
    vi.unstubAllGlobals();
});

describe("useVisionCamera", () => {
    it("开启摄像头后协商能力，并按请求回传压缩后的单帧", async () => {
        const dom = installCameraDomMocks();
        restoreMediaDevices = dom.restoreMediaDevices;
        const ws = createFakeWebSocket();
        const wrapper = mountCamera(ws);

        await activeCamera!.toggle();

        expect(dom.getUserMedia).toHaveBeenCalledWith({
            audio: false,
            video: {
                facingMode: "user",
                width: { ideal: 1280 },
                height: { ideal: 720 },
            },
        });
        expect(activeCamera!.enabled.value).toBe(true);
        expect(ws.sent).toContainEqual(
            expect.objectContaining({
                type: "client_hello",
                data: { capabilities: ["camera"] },
            }),
        );

        ws.emit(
            serverEvent("camera_capture_request", {
                capture_id: "capture-1",
                question: "用户在做什么？",
                reason: "用户询问当前画面",
                timeout_s: 20,
            }),
        );
        await flushPromises();

        expect(dom.canvas.width).toBe(1024);
        expect(dom.canvas.height).toBe(576);
        expect(dom.drawImage).toHaveBeenCalledOnce();
        expect(ws.sent).toContainEqual(
            expect.objectContaining({
                type: "camera_capture_result",
                request_id: "capture-1",
                data: expect.objectContaining({
                    capture_id: "capture-1",
                    ok: true,
                    mime_type: "image/jpeg",
                    image: "ZmFrZS1qcGVn",
                }),
            }),
        );

        wrapper.unmount();
        expect(dom.stopTrack).toHaveBeenCalled();
    });

    it("拒绝摄像头权限时向用户显示明确提示", async () => {
        const dom = installCameraDomMocks();
        restoreMediaDevices = dom.restoreMediaDevices;
        dom.getUserMedia.mockRejectedValueOnce(new Error("NotAllowedError: Permission denied"));
        const alert = dom.alert;
        const ws = createFakeWebSocket();
        const wrapper = mountCamera(ws);

        await activeCamera!.toggle();

        expect(activeCamera!.enabled.value).toBe(false);
        expect(activeCamera!.error.value).toContain("摄像头权限被拒绝");
        expect(alert).toHaveBeenCalledWith(expect.stringContaining("摄像头权限被拒绝"));
        wrapper.unmount();
    });

    it("连接断开时停止摄像头轨道", async () => {
        const dom = installCameraDomMocks();
        restoreMediaDevices = dom.restoreMediaDevices;
        const ws = createFakeWebSocket();
        const wrapper = mountCamera(ws);

        await activeCamera!.toggle();
        ws.status.value = "disconnected";
        await flushPromises();

        expect(activeCamera!.enabled.value).toBe(false);
        expect(dom.stopTrack).toHaveBeenCalled();
        wrapper.unmount();
    });
});
