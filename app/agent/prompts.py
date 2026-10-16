"""
专家 system prompt 构造。

每个专家有不同的职责提示词；人格、情绪格式等公共部分在这里拼接。
顺序是「人格 → 职责 → 情绪格式」：情绪格式放最后，离生成位置最近，
模型的格式遵循率更高。
"""

from __future__ import annotations

from app.agent.emotion import EMOTION_INSTRUCTION

# 信息查询专家的职责提示。
# info 已经接了工具（use_tools=True）：有工具就用工具拿实时信息，
# 查不到或没工具时宁可说不确定，也不要编一个结果出来。
_INFO_ROLE_HINT = """\
【当前专家：信息查询】
你正在处理一个信息查询请求。
- 简洁、准确，不绕弯子
- 只依据你确实掌握的信息回答，不确定就说不确定，不要编造
- 如果系统提供了工具，优先调用工具获取实时信息；拿到结果后直接回答，
  不要说"我查一下"之类的过场话
- 查不到就直说"""


def build_chat_prompt(persona_prompt: str) -> str:
    """闲聊专家：人格 + 情绪格式。"""

    parts: list[str] = []
    if persona_prompt:
        parts.append(persona_prompt)
    parts.append(EMOTION_INSTRUCTION)
    return "\n\n".join(parts)


def build_info_prompt(persona_prompt: str) -> str:
    """信息查询专家：人格 + 职责 + 情绪格式。"""

    parts: list[str] = []
    if persona_prompt:
        parts.append(persona_prompt)
    parts.append(_INFO_ROLE_HINT)
    parts.append(EMOTION_INSTRUCTION)
    return "\n\n".join(parts)
