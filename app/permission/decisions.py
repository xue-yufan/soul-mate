from __future__ import annotations

from enum import Enum


class Decision(str, Enum):
    """权限决策"""

    ALLOW = "allow"
    CONFIRM = "confirm"
    DENY = "deny"
