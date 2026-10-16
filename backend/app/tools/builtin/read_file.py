from __future__ import annotations

from pathlib import Path

from app.logging_config import get_logger
from app.tools.base import BaseTool, ToolContext, ToolResult

logger = get_logger("tools.read_file")


class ReadFileTool(BaseTool):
    name = "read_file"
    description = (
        "读取本地文本文件内容。只在用户明确要求查看文件时调用"
        "需要用户确认，仅支持 UTF-8 文本，最大 64KB"
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件的绝对路径"},
        },
        "required": ["path"],
    }
    risk_level = "confirm"

    # 实例状态：单次读取上限
    MAX_BYTES = 64 * 1024

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        raw_path = args.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return ToolResult(ok=False, content="", error="path 参数缺失")

        target = Path(raw_path.strip()).expanduser().resolve()

        allowed = ctx.settings.tools.allowed_dirs
        if allowed:
            allowed_paths = [Path(d).expanduser().resolve() for d in allowed]
            if not any(self._is_under(target, base) for base in allowed_paths):
                return ToolResult(
                    ok=False, content="",
                    error=f"路径不在允许范围内：{target}",
                )

        if not target.exists():
            return ToolResult(ok=False, content="", error=f"文件不存在：{target}")
        if not target.is_file():
            return ToolResult(ok=False, content="", error=f"不是文件：{target}")

        size = target.stat().st_size
        if size > self.MAX_BYTES:
            return ToolResult(
                ok=False, content="",
                error=f"文件过大（{size} 字节），上限 {self.MAX_BYTES}",
            )

        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(ok=False, content="", error="不是 UTF-8 文本文件")
        except Exception as e:
            logger.exception("读取文件失败：%s", target)
            return ToolResult(ok=False, content="", error=str(e))

        return ToolResult(
            ok=True,
            content=f"文件：{target}\n\n{text}",
            meta={"path": str(target), "size": size},
        )

    @staticmethod
    def _is_under(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False


read_file_tool = ReadFileTool()
