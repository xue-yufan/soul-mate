from __future__ import annotations

from pydantic import BaseModel, Field


class PersonalityConfig(BaseModel):
    core_traits: list[str] = Field(default_factory=list)
    speech_style: list[str] = Field(default_factory=list)


class RelationshipConfig(BaseModel):
    default: str = ""
    on_touch_request: str = ""
    on_affirmation: str = ""


class ToolConfirmConfig(BaseModel):
    """
    工具确认时的口吻模板

    - prompts：按工具名分组的候选句，取不到就用 default，再取不到用代码兜底
      模板里可用占位符：{tool}（工具名）、{detail}（参数的人话摘要）
    - declined：用户拒绝或确认超时后的收场白
    """

    prompts: dict[str, list[str]] = Field(default_factory=dict)
    declined: list[str] = Field(default_factory=list)


class PersonaProfile(BaseModel):
    """单个人格的结构化定义"""

    schema_version: int = 1
    name: str
    aliases: list[str] = Field(default_factory=list)
    background: str = ""
    personality: PersonalityConfig = PersonalityConfig()
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    taboos: list[str] = Field(default_factory=list)
    relationship: RelationshipConfig = RelationshipConfig()
    tool_confirm: ToolConfirmConfig = ToolConfirmConfig()
    rules: list[str] = Field(default_factory=list)
    sample_dialogues: dict[str, str] = Field(default_factory=dict)
