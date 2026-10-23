from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.logging_config import get_logger

logger = get_logger("vision.observer")

_USER_PROMPT = """\
观察当前摄像头画面：{question}
只输出不超过 80 个汉字的可见事实，不猜身份、年龄、健康等敏感信息。"""


class VisionObservationError(RuntimeError):
    """视觉模型不可用或没有返回有效观察。"""


@dataclass(frozen=True)
class VisionObservation:
    summary: str
    details: list[str]
    people_count: int
    objects: list[str]
    activity: str
    uncertainties: list[str]
    confidence: float

    def to_text(self) -> str:
        parts = [f"观察：{self.summary}"]
        if self.activity:
            parts.append(f"可见活动：{self.activity}")
        if self.objects:
            parts.append("可见物体：" + "、".join(self.objects[:12]))
        if self.details:
            parts.append("细节：" + "；".join(self.details[:8]))
        if self.uncertainties:
            parts.append("不确定：" + "；".join(self.uncertainties[:6]))
        parts.append(f"置信度：{self.confidence:.2f}")
        return "\n".join(parts)


def _string_list(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        if len(out) >= limit:
            break
    return out


def _extract_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _parse_observation(content: str) -> VisionObservation:
    data = _extract_json(content)
    if data is None:
        summary = content.strip()
        if not summary:
            raise VisionObservationError("视觉模型没有返回观察内容")
        return VisionObservation(
            summary=summary[:1200],
            details=[],
            people_count=0,
            objects=[],
            activity="",
            uncertainties=[],
            confidence=0.6,
        )

    summary = data.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise VisionObservationError("视觉模型返回的观察缺少 summary")

    count = data.get("people_count", 0)
    if isinstance(count, bool) or not isinstance(count, int):
        count = 0
    count = max(0, min(count, 50))

    confidence = data.get("confidence", 0.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = 0.0
    confidence = max(0.0, min(float(confidence), 1.0))

    activity = data.get("activity")
    if not isinstance(activity, str):
        activity = ""

    return VisionObservation(
        summary=summary.strip()[:1200],
        details=_string_list(data.get("details")),
        people_count=count,
        objects=_string_list(data.get("objects")),
        activity=activity.strip()[:300],
        uncertainties=_string_list(data.get("uncertainties"), limit=6),
        confidence=confidence,
    )


class VisionObserver:
    """调用本机 Ollama 视觉模型，把图片转换成可交给文本 Agent 的观察。"""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport

    async def observe(
        self,
        image: bytes,
        *,
        question: str,
        mime_type: str = "image/jpeg",
    ) -> VisionObservation:
        cfg = self.settings.vision
        if len(image) > cfg.max_image_bytes:
            raise VisionObservationError(
                f"图片过大（{len(image)} 字节），上限 {cfg.max_image_bytes}"
            )

        b64 = base64.b64encode(image).decode("ascii")
        payload = {
            "model": cfg.model,
            "messages": [
                {
                    "role": "user",
                    "content": _USER_PROMPT.format(question=question.strip() or "描述当前画面"),
                    "images": [b64],
                },
            ],
            "stream": False,
            "think": False,
            "keep_alive": cfg.keep_alive,
            "options": {
                "num_ctx": cfg.num_ctx,
                "num_predict": cfg.num_predict,
                "temperature": 0.1,
            },
        }

        url = cfg.base_url.rstrip("/") + "/api/chat"
        timeout = httpx.Timeout(cfg.timeout_s)
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("视觉模型调用失败：%s", exc)
            raise VisionObservationError(
                "本机视觉模型暂时不可用，请确认 Ollama 已启动且模型已下载"
            ) from exc

        message = body.get("message") if isinstance(body, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise VisionObservationError("视觉模型没有返回最终观察内容")

        observation = _parse_observation(content)
        logger.info(
            "视觉观察完成：model=%s image_type=%s confidence=%.2f",
            cfg.model,
            mime_type,
            observation.confidence,
        )
        return observation
