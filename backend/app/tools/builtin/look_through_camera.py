from __future__ import annotations

from app.logging_config import get_logger
from app.tools.base import BaseTool, ToolContext, ToolResult
from app.vision import (
    CameraCaptureError,
    CameraUnavailableError,
    VisionObservationError,
    VisionObserver,
)

logger = get_logger("tools.look_through_camera")


class LookThroughCameraTool(BaseTool):
    name = "look_through_camera"
    description = (
        "当用户询问你眼前看到什么、用户当前的动作或周围环境，"
        "或者只有看一帧摄像头画面才能准确回答时调用。"
        "普通闲聊不要调用。每次用户请求最多调用一次。"
        "画面由用户明确开启的摄像头提供；不能用于身份识别。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "希望从当前画面确认的具体问题",
            },
            "reason": {
                "type": "string",
                "description": "为什么必须看当前画面才能回答",
            },
        },
        "required": ["question"],
    }
    risk_level = "confirm"

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        question = args.get("question")
        if not isinstance(question, str) or not question.strip():
            return ToolResult(ok=False, content="", error="question 参数缺失")

        reason = args.get("reason")
        if not isinstance(reason, str):
            reason = ""

        if not ctx.settings.features.vision:
            return ToolResult(ok=False, content="", error="视觉能力未开启")
        if ctx.camera_broker is None:
            return ToolResult(ok=False, content="", error="摄像头通道未初始化")

        try:
            frame = await ctx.camera_broker.request_capture(
                version=ctx.settings.contracts.ws_event_version,
                request_id=ctx.request_id or "camera",
                session_id=ctx.session_id,
                question=question.strip(),
                reason=reason.strip(),
                timeout_s=ctx.settings.vision.capture_timeout_s,
            )
        except CameraUnavailableError as exc:
            return ToolResult(ok=False, content="", error=str(exc))
        except CameraCaptureError as exc:
            return ToolResult(ok=False, content="", error=str(exc))
        except Exception as exc:
            logger.exception("请求摄像头抓帧失败")
            return ToolResult(ok=False, content="", error=f"摄像头抓帧失败：{exc}")

        if frame.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            return ToolResult(
                ok=False,
                content="",
                error=f"不支持的图片类型：{frame.mime_type}",
            )

        try:
            observation = await VisionObserver(ctx.settings).observe(
                frame.image,
                question=question,
                mime_type=frame.mime_type,
            )
        except VisionObservationError as exc:
            return ToolResult(ok=False, content="", error=str(exc))
        except Exception as exc:
            logger.exception("视觉模型观察失败")
            return ToolResult(ok=False, content="", error=f"视觉观察失败：{exc}")

        return ToolResult(
            ok=True,
            content=observation.to_text(),
            meta={
                "mime_type": frame.mime_type,
                "image_bytes": len(frame.image),
                "confidence": observation.confidence,
                "people_count": observation.people_count,
            },
        )


look_through_camera_tool = LookThroughCameraTool()
