"""人格模块"""

from __future__ import annotations

from app.persona.config import (
    PersonalityConfig,
    PersonaProfile,
    RelationshipConfig,
)
from app.persona.loader import (
    PersonaStore,
    build_system_prompt,
    load_persona,
)

__all__ = [
    "PersonaProfile",
    "PersonalityConfig",
    "RelationshipConfig",
    "PersonaStore",
    "build_system_prompt",
    "load_persona",
]
