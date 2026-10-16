"""父进程看门狗

Electron 启动后端时通过环境变量 SOUL_MATE_PARENT_PID 把自己的 pid 传进来。
本模块轮询父进程是否还活着：父进程一旦消失（正常关闭、崩溃、被任务管理器强杀、
electron-vite 热重启主进程），就主动走应用层优雅停机并退出，避免后端变成孤儿进程
一直占着 8000 端口。

手动启动（scripts/run_dev.ps1、直接敲 uvicorn）不会设置该环境变量，看门狗自动停用，
不会影响手动调试。
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Awaitable, Callable, Optional

from app.logging_config import get_logger

logger = get_logger(__name__)

PARENT_PID_ENV = "SOUL_MATE_PARENT_PID"

# 轮询间隔：够短能及时退出，够长不浪费 CPU
POLL_INTERVAL_S = 2.0

# 父进程消失后，最多给优雅停机留这么久
SHUTDOWN_GRACE_S = 8.0


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _SYNCHRONIZE = 0x00100000
    _ERROR_ACCESS_DENIED = 5
    _WAIT_TIMEOUT = 0x00000102

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def _is_alive(pid: int) -> bool:
        """Windows：拿进程句柄再等 0ms，能区分「进程不存在」和「进程已退出」"""

        handle = _kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            # 拒绝访问说明进程还在，只是当前身份看不到它的句柄
            if ctypes.get_last_error() == _ERROR_ACCESS_DENIED:
                return True
            return False
        try:
            return _kernel32.WaitForSingleObject(handle, 0) == _WAIT_TIMEOUT
        finally:
            _kernel32.CloseHandle(handle)

else:

    def _is_alive(pid: int) -> bool:
        """POSIX：信号 0 只做存在性 / 权限检查"""

        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def read_parent_pid() -> Optional[int]:
    """从环境变量读父进程 pid；没设置或非法时返回 None（表示不启用看门狗）"""

    raw = os.environ.get(PARENT_PID_ENV, "").strip()
    if not raw:
        return None

    try:
        pid = int(raw)
    except ValueError:
        logger.warning("%s=%r 不是合法 pid，父进程看门狗停用", PARENT_PID_ENV, raw)
        return None

    if pid <= 0 or pid == os.getpid():
        logger.warning("%s=%r 不合法，父进程看门狗停用", PARENT_PID_ENV, raw)
        return None

    return pid


async def watch_parent(
    pid: int,
    on_parent_gone: Callable[[], Awaitable[None]],
) -> None:
    """轮询父进程；消失后执行 on_parent_gone，然后结束当前进程"""

    logger.info("父进程看门狗已启动：parent_pid=%s", pid)

    while True:
        await asyncio.sleep(POLL_INTERVAL_S)
        if _is_alive(pid):
            continue
        logger.warning("父进程 %s 已消失，开始优雅停机", pid)
        break

    try:
        await asyncio.wait_for(on_parent_gone(), timeout=SHUTDOWN_GRACE_S)
    except Exception:
        logger.exception("父进程消失后的优雅停机失败")
    finally:
        logger.info("看门狗退出进程")
        # 应用层停机已经跑完，这里跳过 uvicorn 自身的收尾直接退出
        os._exit(0)


def start_parent_watchdog(
    on_parent_gone: Callable[[], Awaitable[None]],
) -> Optional[asyncio.Task]:
    """按需启动看门狗；未设置父进程 pid 时返回 None"""

    pid = read_parent_pid()
    if pid is None:
        logger.info("未设置 %s，父进程看门狗停用（手动启动模式）", PARENT_PID_ENV)
        return None

    return asyncio.create_task(
        watch_parent(pid, on_parent_gone),
        name="parent-watchdog",
    )
