from __future__ import annotations

from app.logging_config import get_logger
from app.tools.base import BaseTool, ToolContext, ToolResult

logger = get_logger("tools.get_weather")


class GetWeatherTool(BaseTool):
    name = "get_weather"
    description = "查询指定城市的当前天气。用户问天气时调用。"
    parameters = {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称，例如：北京、上海",
            },
        },
        "required": ["city"],
    }
    risk_level = "safe"

    # 实例状态：mock 数据
    _MOCK = {
        "北京": "晴，气温 18~26°C，微风",
        "上海": "多云，气温 20~27°C，东南风 3 级",
        "广州": "阵雨，气温 24~30°C，南风 2 级",
        "深圳": "阴，气温 25~31°C，无持续风向",
        "杭州": "晴转多云，气温 19~28°C，微风",
    }

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        city = args.get("city")
        if not isinstance(city, str) or not city.strip():
            return ToolResult(ok=False, content="", error="city 参数缺失")

        city = city.strip()
        weather = self._MOCK.get(city)
        if weather is None:
            logger.debug("mock 未收录城市：%s", city)
            weather = "暂无数据（mock 模式，v0.6 接真实 API）"

        return ToolResult(ok=True, content=f"{city}：{weather}")


get_weather_tool = GetWeatherTool()
