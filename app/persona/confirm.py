"""工具确认 / 拒绝时的口吻渲染

确认不是系统弹窗，而是"她自己开口问一句"。语句写在 persona.yaml 的
tool_confirm 段里（可给每个工具写多套候选），本模块只负责挑选与填充，
保证措辞始终来自人格作者，而不是模型现场发挥。
"""

from __future__ import annotations

import json
import random
from typing import Any, Mapping, Sequence

from app.persona.config import PersonaProfile

# 人格里没配模板时的兜底，保证流程不会因为缺文案而卡住
_FALLBACK_PROMPT = "接下来我想动用「{tool}」，阁下可准许？"
_FALLBACK_DECLINED = "……好，那便不动它了。"

# 各个工具「参数怎么说成人话」：按顺序取第一个非空的字符串字段
_DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    "read_file": ("path",),
}

_MAX_DETAIL_LEN = 200


def _detail(tool_name: str, args: Mapping[str, Any]) -> str:
    """把参数压成一句短语，优先用该工具最关键的字段"""

    for key in _DETAIL_KEYS.get(tool_name, ()):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if not args:
        return "（无参数）"

    try:
        text = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(args)

    if len(text) > _MAX_DETAIL_LEN:
        text = text[:_MAX_DETAIL_LEN] + "…"
    return text


def _pick(candidates: Sequence[str] | None, fallback: str) -> str:
    """从候选句里随机挑一句，都没有就用兜底"""

    usable = [c for c in (candidates or []) if c and c.strip()]
    if not usable:
        return fallback
    return random.choice(usable)


def render_confirm_prompt(
    persona: PersonaProfile | None,
    tool_name: str,
    args: Mapping[str, Any],
) -> str:
    """渲染"她开口请求确认"的那句话"""

    prompts = persona.tool_confirm.prompts if persona is not None else {}

    template = _pick(prompts.get(tool_name), "")
    if not template:
        template = _pick(prompts.get("default"), _FALLBACK_PROMPT)

    # 用 replace 而不是 str.format：模板里出现别的花括号时不会炸
    return (
        template
        .replace("{tool}", tool_name)
        .replace("{detail}", _detail(tool_name, args))
    )


def render_declined(persona: PersonaProfile | None) -> str:
    """渲染"她收手"的那句话（用户拒绝或确认超时后）"""

    lines = persona.tool_confirm.declined if persona is not None else None
    return _pick(lines, _FALLBACK_DECLINED)
