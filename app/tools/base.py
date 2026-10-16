from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from app.config import Settings

RiskLevel = Literal["safe", "confirm", "dangerous"]


@dataclass
class ToolContext:
    """工具运行时上下文"""

    settings: Settings
    session_id: str
    lifecycle: Any = None


@dataclass
class ToolResult:
    """工具执行结果"""

    ok: bool
    content: str
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class BaseTool(ABC):
    """工具基类"""

    # 子类必须覆盖
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    risk_level: RiskLevel = "safe"

    def __init__(self) -> None:
        if not self.name:
            raise ValueError(f"{type(self).__name__} 未定义 name")
        if not self.description:
            raise ValueError(f"{type(self).__name__} 未定义 description")

    @abstractmethod
    async def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """执行工具：子类实现"""

        raise NotImplementedError

    def to_openai_schema(self) -> dict[str, Any]:
        """转成 OpenAI function calling 的 tools 格式"""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:
        return f"<Tool {self.name} risk={self.risk_level}>"
