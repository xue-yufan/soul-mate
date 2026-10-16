from __future__ import annotations

from datetime import datetime, timezone

from app.tools.base import BaseTool, ToolContext, ToolResult


class GetTimeTool(BaseTool):
    name = "get_time"
    description = "获取当前本地时间和 UTC 时间。用户问时间、日期时调用。"
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    risk_level = "safe"

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        now_local = datetime.now().astimezone()
        now_utc = datetime.now(timezone.utc)
        content = (
            f"本地时间：{now_local.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"UTC 时间：{now_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        return ToolResult(ok=True, content=content)


# 模块级实例，供注册表使用
get_time_tool = GetTimeTool()
