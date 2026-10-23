from __future__ import annotations

from app.logging_config import get_logger
from app.tools.base import BaseTool

logger = get_logger("tools.registry")


class ToolRegistry:
    """工具注册表"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具已注册：{tool.name}")
        self._tools[tool.name] = tool
        logger.debug("工具已注册：%s risk=%s", tool.name, tool.risk_level)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_all(self) -> list[BaseTool]:
        return list(self._tools.values())

    def to_openai_schemas(self) -> list[dict]:
        """导出所有工具的 OpenAI function calling schema"""

        return [t.to_openai_schema() for t in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def clear(self) -> None:
        """清空，供测试用"""

        self._tools.clear()


# ---------- 全局单例 ----------

_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """获取全局注册表单例"""

    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def reset_registry() -> None:
    """重置单例，供测试用"""

    global _registry
    _registry = None


def register_builtin_tools(*, include_vision: bool = False) -> None:
    """注册所有内置工具。启动时调用一次"""

    from app.tools.builtin.get_time import get_time_tool
    from app.tools.builtin.get_weather import get_weather_tool
    from app.tools.builtin.look_through_camera import look_through_camera_tool
    from app.tools.builtin.read_file import read_file_tool

    registry = get_registry()
    builtins = [get_time_tool, get_weather_tool, read_file_tool]
    if include_vision:
        builtins.append(look_through_camera_tool)
    for tool in builtins:
        if tool.name not in registry.names():
            registry.register(tool)
    logger.info("内置工具已注册：%s", registry.names())
