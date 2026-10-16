from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncGenerator, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    """模型请求的一次工具调用。"""

    id: str
    name: str
    arguments: str


@dataclass
class ChatMessage:
    """统一消息结构"""

    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None

    def to_openai_dict(self) -> dict:
        """转成 OpenAI chat completions 的消息格式"""

        d: dict = {"role": self.role, "content": self.content or ""}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in self.tool_calls
            ]
        return d


@dataclass
class StreamChunk:
    """流式片段"""

    delta: str = ""
    finish_reason: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    tool_calls: list[ToolCall] | None = None


class ModelAdapter(ABC):
    """模型适配器基类"""

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict] | None = None
    ) -> AsyncGenerator[StreamChunk]:
        """流式对话，逐块产出"""

        raise NotImplementedError
        yield

    async def aclose(self) -> None:
        """释放底层资源"""

        return None
