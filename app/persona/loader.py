from __future__ import annotations

from pathlib import Path

import yaml

from app.logging_config import get_logger
from app.persona.config import PersonaProfile

logger = get_logger(__name__)


def load_persona(path: Path) -> PersonaProfile:
    """从 YAML 加载人格配置，失败抛异常"""

    if not path.exists():
        raise FileNotFoundError(f"人格文件不存在：{path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"人格文件格式错误：{path}")

    persona = PersonaProfile.model_validate(raw)
    logger.info("人格已加载：%s (%s)", persona.name, path)
    return persona

def _bullet(items: list[str]) -> str:
    """将字符串列表转换成带 Bullet 的字符串文本"""

    return "\n".join(f"- {item}" for item in items)

def build_system_prompt(persona: PersonaProfile) -> str:
    """把结构化人格拼成 system prompt"""

    parts: list[str] = []

    parts.append(f"你是{persona.name}。")
    if persona.aliases:
        parts.append(f"别名：{'、'.join(persona.aliases)}。")

    if persona.background:
        parts.append(f"【背景】\n{persona.background.strip()}")

    if persona.personality.core_traits:
        parts.append("【性格】\n" + _bullet(persona.personality.core_traits))

    if persona.personality.speech_style:
        parts.append("【说话方式】\n" + _bullet(persona.personality.speech_style))

    if persona.likes:
        parts.append("【喜好】\n" + _bullet(persona.likes))

    if persona.dislikes:
        parts.append("【不喜欢】\n" + _bullet(persona.dislikes))

    if persona.taboos:
        parts.append("【禁忌】\n" + _bullet(persona.taboos))

    if persona.relationship.default:
        parts.append(f"【与用户的关系】\n{persona.relationship.default.strip()}")

    if persona.relationship.on_touch_request:
        parts.append(f"【被要求肢体接触时】\n{persona.relationship.on_touch_request.strip()}")

    if persona.relationship.on_affirmation:
        parts.append(f"【被肯定时】\n{persona.relationship.on_affirmation.strip()}")

    if persona.rules:
        parts.append("【行为准则】\n" + _bullet(persona.rules))

    return "\n\n".join(parts)

class PersonaStore:
    """人格存储：加载一次，缓存 Prompt"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.persona: PersonaProfile | None = None
        self._prompt: str | None = None

    def load(self) -> None:
        """加载人格并缓存 Prompt"""
        self.persona = load_persona(self.path)
        self._prompt = build_system_prompt(self.persona)

    @property
    def prompt(self) -> str:
        if self._prompt is None:
            raise RuntimeError("人格尚未加载，请先调用 load()")
        return self._prompt

    @property
    def name(self) -> str:
        if self.persona is None:
            raise RuntimeError("人格尚未加载，请先调用 load()")
        return self.persona.name
