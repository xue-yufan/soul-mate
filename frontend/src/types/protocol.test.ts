import { describe, expect, it } from "vitest";

import { SERVER_EVENT_TYPES, isServerEvent } from "@/types/protocol";
import { serverEvent } from "@/test/helpers";

describe("protocol", () => {
    it("registers camera capture requests as a server event", () => {
        expect(SERVER_EVENT_TYPES.has("camera_capture_request")).toBe(true);
    });

    it("recognizes a supported server envelope", () => {
        const event = serverEvent("camera_capture_request", {
            capture_id: "capture-1",
            question: "用户在做什么？",
            reason: "需要观察当前画面",
            timeout_s: 20,
        });

        expect(isServerEvent(event)).toBe(true);
    });

    it("rejects an unsupported protocol version", () => {
        expect(
            isServerEvent({
                v: 999,
                type: "camera_capture_request",
                data: {},
            }),
        ).toBe(false);
    });
});
