"""进程级运行时标识

每次进程启动生成一个新的 boot_id，用来区分「同一个后端进程」和「重启过的新进程」。
Electron 侧靠它判断 8000 端口上那个后端是不是自己拉起来的、跑的是不是当前这次启动的代码。
"""

from __future__ import annotations

import os
import uuid

# uvicorn --reload 下每个 worker 进程重新 import 本模块，因此会拿到各自的新 boot_id
BOOT_ID = uuid.uuid4().hex[:12]


def process_pid() -> int:
    """当前服务进程的 pid（--reload 下是真正处理请求的 worker）"""

    return os.getpid()
