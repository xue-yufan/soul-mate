from __future__ import annotations

import asyncio
import os
import signal
import time
from contextlib import asynccontextmanager
from types import FrameType
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agent import AgentRunner, build_graph
from app.api.config import router as config_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.history import router as history_router
from app.api.memory import router as memory_router
from app.api.worldbook import router as worldbook_router
from app.api.ws import router as ws_router
from app.config import load_settings
from app.lifecycle import Lifecycle
from app.logging_config import get_logger, setup_logging
from app.memory.embedder import Embedder, get_embedder
from app.models import build_adapter
from app.parent_watchdog import start_parent_watchdog
from app.persona.loader import PersonaStore
from app.runtime import BOOT_ID
from app.storage.db_SQlite import init_db
from app.storage.session import ensure_session
from app.tools import get_registry, register_builtin_tools
from app.tts import build_tts
from app.tts.engine import TtsEngine
from app.vision import CameraBroker

logger = get_logger(__name__)

# 仅用于应用元信息、CORS 与 __main__ 的监听地址：这些必须在启动前确定。
# 真正的运行配置在 lifespan 里按启动时的环境重新加载，避免 import 时就固化，
# 也避免测试/多环境场景下被 import 时刻的环境与工作目录带偏。
_bootstrap_settings = load_settings()


def _install_signal_handlers(
    lifecycle: Lifecycle,
    loop: asyncio.AbstractEventLoop,
) -> dict[int, Any]:
    """接管 SIGINT / SIGTERM，先跑完应用层停机，再放行 Uvicorn"""

    original_handlers: dict[int, Any] = {}

    def _make_handler(sig: int) -> Any:
        def _on_signal(signum: int, _frame: FrameType | None) -> None:
            if lifecycle.is_shutting_down:
                # 已经在关，忽略重复信号
                return
            logger.info("收到信号 %s，开始应用层停机", signum)

            async def _run():
                try:
                    await lifecycle.shutdown()
                finally:
                    # 恢复 Uvicorn 的处理器，重新触发信号
                    handler = original_handlers.get(sig)
                    if handler is not None:
                        signal.signal(sig, handler)
                    os.kill(os.getpid(), sig)

            loop.create_task(_run())

        return _on_signal

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            original_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _make_handler(sig))
        except (ValueError, OSError) as e:
            # 非主线程或平台不支持
            logger.warning("注册信号处理器失败：sig=%s, err=%s", sig, e)

    return original_handlers

@asynccontextmanager
async def lifespan(app: FastAPI):
    """服务生命周期：启动时加载配置、初始化数据库与日志，关闭时优雅停机"""

    settings = load_settings()
    setup_logging(settings)
    app.state.settings = settings

    app.state.start_time = time.monotonic()
    app.state.boot_id = BOOT_ID

    # 优雅停机管理器：尽早建好，父进程看门狗和信号处理都要用
    lifecycle = Lifecycle(
        version=settings.contracts.ws_event_version,
        shutdown_timeout=settings.server.shutdown_timeout_s,
        ws_notify_delay=settings.server.ws_notify_delay_s,
    )
    app.state.lifecycle = lifecycle
    app.state.camera_broker = CameraBroker()

    # 父进程看门狗：Electron 挂掉（正常关、崩溃、被强杀）后自己退出，避免变成孤儿进程占着端口
    watchdog_task = start_parent_watchdog(lifecycle.shutdown)

    logger.info("服务启动：%s v%s", settings.app.name, settings.app.version)
    logger.info("运行环境：%s", settings.app.env)
    logger.info("启动标识：boot_id=%s pid=%s", BOOT_ID, os.getpid())

    await init_db(settings)
    logger.info("数据库已初始化：%s", settings.paths.sqlite_path)

    # 单用户单会话：确保默认会话存在
    await ensure_session(settings, settings.agent.default_session_id)
    logger.info("默认会话已就绪：%s", settings.agent.default_session_id)

    # 人格
    persona_store = PersonaStore(settings.persona.file)
    persona_store.load()
    logger.info("人格已加载：%s", persona_store.name)
    app.state.persona_store = persona_store

    # 模型适配器
    adapter = build_adapter(settings)
    app.state.model_adapter = adapter
    logger.info("模型已装配：adapter=%s, model=%s", settings.model.adapter, settings.model.model)

    # TTS
    if settings.features.tts:
        tts_adapter = build_tts(settings)
        app.state.tts_adapter = tts_adapter
        logger.info("TTS 已装配：provider=%s voice=%s", settings.tts.provider, settings.tts.voice)
    else:
        tts_adapter = None
        app.state.tts_adapter = None
        logger.info("TTS 未启用（features.tts=false）")

    # 本地 TTS 引擎（GPT-SoVITS 之类）：交给 lifecycle 后台拉起，绝不阻塞启动——
    # 等模型加载要几十秒，后端 /health 不能等它，否则主应用会判"后端启动超时"。
    # 只在 provider == gpt_sovits 且 tts.engine_dir 非空时启用，而且先探测再启动。
    tts_engine = TtsEngine(settings)
    tts_engine.start(lifecycle)

    # 记忆：校验配置并预热嵌入模型
    #
    # 首次加载要十几秒（transformers/torch 导入 + 权重载入），放在启动阶段完成，
    # 不要拖到用户的第一句话里。预热失败只降级记忆/知识召回，不影响服务启动。
    if settings.features.memory or settings.features.rag:
        try:
            Embedder.ensure_configured(settings)
            embedder = get_embedder(settings)
            await embedder.warmup()
            app.state.memory_ready = True
            logger.info("嵌入模型已预热：dim=%s", embedder.dimension)
        except Exception as e:
            get_embedder(settings).mark_unavailable(str(e) or e.__class__.__name__)
            app.state.memory_ready = False
            logger.exception("嵌入模型预热失败，记忆/知识召回降级为不可用")
    else:
        app.state.memory_ready = False
        logger.info("记忆与知识库未启用（features.memory=false, features.rag=false）")

    # 注册工具
    if settings.features.tools:
        register_builtin_tools(
            include_vision=settings.features.vision,
        )
        registry = get_registry()
        openai_schemas = registry.to_openai_schemas()
        logger.info("工具已启用：%s", registry.names())
    else:
        openai_schemas = []
        logger.info("工具未启用（features.tools=false）")
    app.state.openai_schemas = openai_schemas

    # Agent 图与 runner
    graph = build_graph()
    runner = AgentRunner(
        graph=graph,
        adapter=adapter,
        persona_prompt=persona_store.prompt,
        persona=persona_store.persona,
        settings=settings,
        openai_schemas=openai_schemas,
    )
    app.state.agent_runner = runner
    logger.info("Agent 图已编译：supervisor -> chat | info")

    # 接管信号，保证在 Uvicorn 关 WebSocket 之前先发 server_shutdown
    loop = asyncio.get_running_loop()
    _install_signal_handlers(lifecycle, loop)

    logger.info(
        "监听地址：http://%s:%d%s",
        settings.server.host,
        settings.server.port,
        settings.server.ws_path
    )

    yield

    if tts_adapter is not None:
        await tts_adapter.aclose()

    await tts_engine.aclose()

    # 非信号触发的退出路径（例如测试直接关闭 lifespan）
    if not lifecycle.is_shutting_down:
        await lifecycle.shutdown()

    # 释放模型适配器的连接池
    try:
        await adapter.aclose()
    except Exception:
        logger.exception("关闭模型适配器失败")

    if watchdog_task is not None and not watchdog_task.done():
        watchdog_task.cancel()

    logger.info("服务关闭：%s", settings.app.name)

app = FastAPI(
    title=_bootstrap_settings.app.name,
    version=_bootstrap_settings.app.version,
    lifespan=lifespan,
)

# 让路由能通过 request.app.state.settings / websocket.app.state.settings 拿到配置
# （lifespan 启动时会用按当前环境重新加载的配置覆盖它）
app.state.settings = _bootstrap_settings

# CORS：本机 Electron 接入前可为空
app.add_middleware(
    CORSMiddleware,
    allow_origins=_bootstrap_settings.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(health_router)
app.include_router(config_router)
app.include_router(history_router)
app.include_router(memory_router)
app.include_router(documents_router)
app.include_router(worldbook_router)
app.include_router(ws_router)


if __name__ == "__main__":
    import uvicorn

    # dev 下自动 reload；prod 下不 reload
    reload = _bootstrap_settings.app.env == "dev"

    uvicorn.run(
        "app.main:app",
        host=_bootstrap_settings.server.host,
        port=_bootstrap_settings.server.port,
        reload=reload,
    )
