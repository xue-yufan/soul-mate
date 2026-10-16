"""本地 TTS 引擎的自动拉起（目前只有 GPT-SoVITS）

只在 features.tts 且 tts.provider == "gpt_sovits" 且 tts.engine_dir 非空时启用。
四条约束都是踩过的坑，别随手改：

1. **绝不阻塞启动**：等它就绪可能要几十秒，所以整条流程跑在 lifecycle 的后台任务里。
   后端 /health 不等引擎——否则主应用那 30 秒健康检查会判"后端启动超时"，
   用户看到的是"应用起不来"，而实际上只是模型在加载。
2. **先探测再启动**：`{base_url}/openapi.json` 的 paths 里有 `/tts` 就认为引擎已在跑、
   直接复用。用户手动起过的那一份不会被撞（撞了就是端口冲突 + 新进程秒退）。
   api_v2 是 `FastAPI()` 且没设 title，所以只能靠 path 认亲。
3. **随应用一起收**：引擎是我们 spawn 的子进程。Electron 收后端时用的 taskkill /T
   会连带收掉它；优雅停机时这里也显式收一次（Windows 上同样要 /T——uv 下面还挂着 python）。
4. **失败只降级**：找不到 uv / 目录不对 / 端口被占 → 记 warning 继续跑，
   对话照常，只是没声音（和 http_base 的降级语义一致）。
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.logging_config import get_logger

logger = get_logger("tts.engine")

#: 跑测试时置位：start() 据此绝不拉起引擎（见那里的注释）
#: import 时算一次就够——pytest 一定先于被测代码导入
_RUNNING_UNDER_PYTEST = "pytest" in sys.modules


#: api_v2 的默认监听地址（也是自动拉起时它实际会绑的端口）
DEFAULT_BASE_URL = "http://127.0.0.1:9880"

#: 项目用 uv 管理，pyproject 里注册了这个 console script
ENGINE_COMMAND = ("uv", "run", "--locked", "gpt-sovits-api-v2")

#: 等它"端口能应答"的上限。注意这不等于"模型加载完了"——api_v2 是在第一次
#: /tts 请求里加载模型的，那一段由 tts.timeout_s 兜着。
READY_TIMEOUT_S = 90.0
READY_POLL_INTERVAL_S = 1.0
PROBE_TIMEOUT_S = 2.0
#: 引擎的 stderr 最多转发多少行（剩下的继续读、但不再打日志，免得刷屏）
MAX_STDERR_LINES = 20


async def is_engine_alive(base_url: str) -> bool:
    """认亲：`{base_url}/openapi.json` 里出现 `/tts` 才算我们的引擎

    端口上坐着别的程序时返回 False，调用方就不会去跟它争。
    """

    url = f"{base_url.rstrip('/')}/openapi.json"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(PROBE_TIMEOUT_S)) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return False
        data = resp.json()
    except Exception:
        # 探测失败就是"没在跑"，不该把异常抛给调用方
        return False

    paths = data.get("paths") if isinstance(data, dict) else None
    return isinstance(paths, dict) and "/tts" in paths


def _port_of(url: str) -> int | None:
    try:
        return urlparse(url).port
    except ValueError:
        return None


def _bundled_uv() -> "str | None":
    """打包版自带的 uv（resources/backend/uv.exe）"""

    candidate = Path(__file__).resolve().parents[2] / "uv.exe"
    return str(candidate) if candidate.is_file() else None


class TtsEngine:


    """把本地引擎拉起来；没配好就什么都不做（对话照常，只是没声音）"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.proc: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return (
            self.settings.features.tts
            and self.settings.tts.provider == "gpt_sovits"
            and bool(self.settings.tts.engine_dir.strip())
            # 测试环境绝不拉起真引擎：它会加载几个 GB 的模型、占几分钟 CPU，
            # 还会跟用户机器上正在跑的那一只抢 9880。conftest 的 tmp_env 设了
            # SOUL_MATE_APP__ENV=test，这里再挡一道，不依赖"哪个 fixture
            # 恰好覆盖了 features.tts"。
            and self.settings.app.env != "test"
        )

    def start(self, lifecycle) -> None:
        """交给 lifecycle 后台跑——这里绝不 await，启动流程不等引擎"""

        if not self.enabled:
            return
        # 兜底闸：pytest 进程里绝不拉起。跑测试时它会在用户机器上加载几个 GB 的模型、
        # 抢 9880、把整轮测试从 80 秒拖成好几分钟；而且这一条不依赖任何 fixture
        # 有没有覆盖配置，绕不过去。
        if _RUNNING_UNDER_PYTEST:
            logger.warning("检测到 pytest 进程，跳过 TTS 引擎自动拉起")
            return
        lifecycle.register_task(self.ensure_running())

    async def ensure_running(self) -> None:
        base_url = (self.settings.tts.base_url or DEFAULT_BASE_URL).rstrip("/")
        engine_dir = Path(self.settings.tts.engine_dir).expanduser()

        if not engine_dir.is_dir():
            logger.warning("TTS 引擎目录不存在，跳过拉起：%s", engine_dir)
            return

        if await is_engine_alive(base_url):
            logger.info("TTS 引擎已在跑，直接复用：%s", base_url)
            return

        default_port = _port_of(DEFAULT_BASE_URL)
        if _port_of(base_url) != default_port:
            logger.warning(
                "tts.base_url 指向 :%s，而自动拉起只能按引擎默认端口 :%s 启动；"
                "这种情况请自己带 -p %s 起引擎，这里不替你起",
                _port_of(base_url),
                default_port,
                _port_of(base_url),
            )
            return

        # 打包版自带 backend/uv.exe（extraResources 塞进去的），但它不在 PATH 上；
        # 找不到 PATH 上的 uv 就回落到那一份
        command = shutil.which(ENGINE_COMMAND[0]) or _bundled_uv()
        if command is None:
            logger.warning(
                "找不到 %s，无法自动拉起 TTS 引擎；手动起：cd %s && %s",
                ENGINE_COMMAND[0],
                engine_dir,
                " ".join(ENGINE_COMMAND),
            )
            return

        logger.info(
            "正在拉起 TTS 引擎：%s（cwd=%s）", " ".join(ENGINE_COMMAND), engine_dir
        )
        started = time.monotonic()
        try:
            self.proc = await asyncio.create_subprocess_exec(
                command,
                *ENGINE_COMMAND[1:],
                cwd=str(engine_dir),
                # 进度条走 stdout，丢掉；报错走 stderr，转发出前若干行
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            logger.warning("拉起 TTS 引擎失败：%s", exc)
            self.proc = None
            return

        self._stderr_task = asyncio.create_task(self._forward_stderr())

        deadline = time.monotonic() + READY_TIMEOUT_S
        while time.monotonic() < deadline:
            if self.proc.returncode is not None:
                logger.warning(
                    "TTS 引擎启动后立刻退出（code=%s）：多半是端口被占或依赖缺失；"
                    "手动跑一次看报错：cd %s && %s",
                    self.proc.returncode,
                    engine_dir,
                    " ".join(ENGINE_COMMAND),
                )
                self.proc = None
                return
            if await is_engine_alive(base_url):
                logger.info(
                    "TTS 引擎已就绪（%.1fs）：%s", time.monotonic() - started, base_url
                )
                return
            await asyncio.sleep(READY_POLL_INTERVAL_S)

        logger.warning("等 TTS 引擎就绪超时（%.0fs），这一轮先不合成", READY_TIMEOUT_S)

    async def _forward_stderr(self) -> None:
        """转发引擎 stderr（限量）。必须一直读到 EOF，否则管道写满会把引擎卡住。"""

        proc = self.proc
        if proc is None or proc.stderr is None:
            return

        forwarded = 0
        while True:
            try:
                line = await proc.stderr.readline()
            except (ValueError, OSError):
                return
            if not line:
                return
            if forwarded < MAX_STDERR_LINES:
                forwarded += 1
                logger.warning("引擎输出：%s", line.decode("utf-8", "replace").rstrip())

    async def aclose(self) -> None:
        """收尾：只收"是我们拉起来的"那一个"""

        proc = self.proc
        self.proc = None
        if proc is None or proc.returncode is not None:
            return

        logger.info("正在关闭 TTS 引擎：pid=%s", proc.pid)
        if sys.platform == "win32":
            # uv 下面还挂着 python，光 terminate 会留下孤儿
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(proc.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            except OSError:
                proc.kill()
            return

        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (ProcessLookupError, asyncio.TimeoutError):
            proc.kill()
