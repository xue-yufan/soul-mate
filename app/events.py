from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def make_event(
    type_: str,
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    data: dict[str, Any] | None = None,
    version: int,
) -> dict[str, Any]:
    """构造统一事件信封"""

    return {
        "v": version,
        "type": type_,
        "request_id": request_id,
        "session_id": session_id,
        "ts": now_iso(),
        "data": data or {},
    }
