from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncGenerator

import httpx

from app.config import ModelConfig
from app.logging_config import get_logger
from app.models.base import ChatMessage, ModelAdapter, StreamChunk, ToolCall

logger = get_logger(__name__)

_CHAT_COMPLETIONS_PATH = "/chat/completions"

# 值得重试的状态码：限流与各类服务端瞬时故障
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class _RetryableError(RuntimeError):
    """可重试的模型调用失败（限流 / 5xx）"""


def _is_retryable(exc: Exception) -> bool:
    """
    判断异常是否值得重试

    网络类错误（连接失败、读写超时、协议中断）与限流 / 5xx 都重试；
    鉴权错、参数错这类 4xx 立刻失败，重试只是白等。
    """

    if isinstance(exc, _RetryableError):
        return True
    # httpx.TransportError 覆盖 ConnectError / *Timeout / RemoteProtocolError 等
    return isinstance(exc, httpx.TransportError)


class OpenAICompatibleAdapter(ModelAdapter):
    """OpenAI 兼容协议适配器

    重试策略：限流 / 5xx / 网络抖动最多重试 max_retries 次，指数退避。
    一旦已经吐出调用方看得见的内容（正文或工具调用）就不再重试——
    重来一遍会把同一段话发给用户两次。
    """

    def __init__(
        self,
        config: ModelConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config

        headers = {"Content-Type": "application/json"}
        api_key = config.api_key.get_secret_value()
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            headers=headers,
            # connect 与 read 分开：断网要快速失败，流式生成又要能等很久
            timeout=httpx.Timeout(
                connect=config.connect_timeout_s,
                read=config.timeout_s,
                write=config.timeout_s,
                pool=config.connect_timeout_s,
            ),
            transport=transport,
        )

    async def chat_stream(
            self,
            messages: list[ChatMessage],
            *,
            tools: list[dict] | None = None,
    ) -> AsyncGenerator[StreamChunk]:
        payload = {
            "model": self.config.model,
            "messages": [m.to_openai_dict() for m in messages],
            "stream": True,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools

        logger.debug(
            "请求模型：model=%s, messages=%d, tools=%d",
            self.config.model, len(messages), len(tools or []),
        )

        started_at = time.perf_counter()
        attempts = max(1, self.config.max_retries + 1)
        # 是否已经把调用方看得见的内容交出去（正文 / 工具调用）
        delivered = False

        for attempt in range(1, attempts + 1):
            stats: dict[str, Any] = {
                "status": None,
                "chunks": 0,
                "first_token_ms": None,
                "finish": None,
            }

            try:
                async for chunk in self._stream_once(
                    payload, started_at=started_at, stats=stats
                ):
                    if chunk.delta or chunk.tool_calls:
                        delivered = True
                    yield chunk
            except Exception as e:
                if delivered or not _is_retryable(e) or attempt >= attempts:
                    logger.error(
                        "模型调用失败：attempts=%d/%d status=%s chunks=%d "
                        "delivered=%s err=%s",
                        attempt, attempts, stats["status"], stats["chunks"],
                        delivered, e,
                    )
                    raise
                delay = self.config.retry_base_delay_s * (2 ** (attempt - 1))
                logger.warning(
                    "模型调用失败，%.2fs 后重试（第 %d/%d 次）：%s",
                    delay, attempt, attempts - 1, e,
                )
                await asyncio.sleep(delay)
                continue

            logger.info(
                "模型响应 status=%s attempts=%d chunks=%d first_token_ms=%s "
                "finish=%s elapsed_ms=%d",
                stats["status"], attempt, stats["chunks"],
                stats["first_token_ms"], stats["finish"],
                int((time.perf_counter() - started_at) * 1000),
            )
            return

    async def _stream_once(
        self,
        payload: dict[str, Any],
        *,
        started_at: float,
        stats: dict[str, Any],
    ) -> AsyncGenerator[StreamChunk]:
        """单次流式请求；可重试的失败原样抛出，由 chat_stream 决定要不要重来"""

        # 累积 tool_calls：index -> {id, name, arguments}
        tool_calls_buffer: dict[int, dict[str, str]] = {}

        async with self._client.stream(
            "POST", _CHAT_COMPLETIONS_PATH, json=payload
        ) as response:
            stats["status"] = response.status_code
            if response.status_code >= 400:
                body = await response.aread()
                text = body.decode("utf-8", errors="replace")
                logger.warning(
                    "模型接口错误 status=%s body=%s",
                    response.status_code, text[:200],
                )
                message = f"模型接口返回 {response.status_code}：{text[:500]}"
                if response.status_code in _RETRYABLE_STATUS:
                    raise _RetryableError(message)
                raise RuntimeError(message)

            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue

                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break

                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning("模型返回非法 JSON 片段：%s", data[:200])
                    continue

                choices = obj.get("choices") or [{}]
                choice = choices[0] if choices else {}
                delta = choice.get("delta") or {}
                content = delta.get("content") or ""
                tc_delta = delta.get("tool_calls") or []

                # 累积 tool_calls 分片
                for tc in tc_delta:
                    idx = tc.get("index", 0)
                    buf = tool_calls_buffer.setdefault(
                        idx,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if tc.get("id"):
                        buf["id"] = tc["id"]
                    func = tc.get("function") or {}
                    if func.get("name"):
                        buf["name"] = func["name"]
                    if func.get("arguments"):
                        buf["arguments"] += func["arguments"]

                finish = choice.get("finish_reason")
                if finish:
                    # 只记最后一次有效值：带 usage 的收尾分片 choices 为空，
                    # 无条件覆盖会把 finish 冲成 None，日志就看不出正常收尾了
                    stats["finish"] = finish

                usage = obj.get("usage") or {}

                chunk = StreamChunk(delta=content, finish_reason=finish)
                if usage:
                    chunk.tokens_in = usage.get("prompt_tokens")
                    chunk.tokens_out = usage.get("completion_tokens")

                yield chunk

                stats["chunks"] += 1
                if stats["first_token_ms"] is None and content:
                    stats["first_token_ms"] = int(
                        (time.perf_counter() - started_at) * 1000
                    )

        # 流结束：如有累积的 tool_calls，作为一个 chunk 发出
        if tool_calls_buffer:
            tool_calls = [
                ToolCall(
                    id=buf["id"],
                    name=buf["name"],
                    arguments=buf["arguments"],
                )
                for _, buf in sorted(tool_calls_buffer.items())
            ]
            logger.debug("模型请求 %d 个工具调用", len(tool_calls))
            yield StreamChunk(tool_calls=tool_calls)

    async def aclose(self) -> None:
        await self._client.aclose()
