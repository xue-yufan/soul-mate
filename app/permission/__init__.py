"""权限模块"""

from __future__ import annotations

from app.permission.checker import check, decide
from app.permission.decisions import Decision
from app.permission.pending import (
    PendingConfirm,
    PendingRegistry,
    get_pending_registry,
    reset_pending_registry,
)

__all__ = [
    "Decision",
    "check",
    "decide",
    "PendingConfirm",
    "PendingRegistry",
    "get_pending_registry",
    "reset_pending_registry",
]
