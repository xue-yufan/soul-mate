from __future__ import annotations

from app.config import Settings
from app.logging_config import get_logger
from app.permission.decisions import Decision
from app.permission.pending import PendingRegistry

logger = get_logger("permission.checker")


def decide(
    settings: Settings,
    tool_name: str,
    risk_level: str,
) -> Decision:
    """
    返回决策，不涉及交互

    优先级：
    1. permission.overrides[tool_name] 存在，用它
    2. permission.default 存在，用它
    3. 按 risk_level 兜底
    """

    overrides = settings.permission.overrides or {}
    if tool_name in overrides:
        raw = overrides[tool_name]
        return Decision(raw)

    default = settings.permission.default
    if default:
        return Decision(default)

    if risk_level == "safe":
        return Decision.ALLOW
    if risk_level == "confirm":
        return Decision.CONFIRM
    if risk_level == "dangerous":
        return Decision.DENY

    logger.warning("未知 risk_level：%s，默认 confirm", risk_level)
    return Decision.CONFIRM


async def check(
    settings: Settings,
    tool_name: str,
    risk_level: str,
    args: dict,
    *,
    request_id: str,
    pending: PendingRegistry | None = None,
) -> bool:
    """
    完整权限检查

    - allow：直接返回 True
    - deny：直接返回 False
    - confirm：创建挂起，等前端回复，超时视为拒绝

    调用方负责把 tool_confirm_request 发给前端
    """

    decision = decide(
        settings,
        tool_name,
        risk_level,
    )

    if decision == Decision.ALLOW:
        logger.debug("权限放行：tool=%s", tool_name)
        return True

    if decision == Decision.DENY:
        logger.info("权限拒绝：tool=%s", tool_name)
        return False

    # confirm：等前端
    if pending is None:
        logger.warning("confirm 决策但无 PendingRegistry，视为拒绝")
        return False

    pending_confirm = await pending.create(request_id, tool_name, args)
    logger.info("等待确认：request_id=%s tool=%s", request_id, tool_name)

    timeout_s = settings.permission.confirm_timeout_s
    allowed = await pending.wait(pending_confirm, timeout_s)

    if allowed is True:
        logger.info("确认通过：request_id=%s tool=%s", request_id, tool_name)
        return True

    logger.info("确认未通过：request_id=%s tool=%s result=%s", request_id, tool_name, allowed)
    return False
