from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.events import now_iso
from app.runtime import BOOT_ID, process_pid
from app.storage.db_SQlite import health_check

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    settings = request.app.state.settings
    db_result = await health_check(settings)

    start_time = getattr(request.app.state, "start_time", None)
    uptime_s = int(time.monotonic() - start_time) if start_time is not None else 0

    overall = "ok" if db_result.get("status") == "ok" else "error"

    body = {
        "status": overall,
        "app": settings.app.name,
        "version": settings.app.version,
        # pid / boot_id 供 Electron 识别「这个后端是不是我拉起的、是不是同一次启动」
        "pid": process_pid(),
        "boot_id": BOOT_ID,
        "time": now_iso(),
        "uptime_s": uptime_s,
        "checks": {"database": db_result},
    }

    status_code = 200 if overall == "ok" else 503
    return JSONResponse(content=body, status_code=status_code)
