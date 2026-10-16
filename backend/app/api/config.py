from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.agent import AgentRunner, build_graph
from app.config import Settings, load_settings
from app.config_schema import (
    ConfigValueError,
    apply_patch,
    build_settings_payload,
)
from app.logging_config import get_logger
from app.models import build_adapter

logger = get_logger("api.config")

router = APIRouter(prefix="/config", tags=["config"])

# 这些键由 lifespan 装配（工具注册表 / TTS 适配器 / 嵌入模型预热），
# PATCH 重建的只有 adapter + 图 + runner，命中它们必须重启进程才生效。
# 剩下的（model.*、permission.*、agent.*、memory.*、rag.* 等）都是请求期读取，
# 重建运行时即可立刻生效。
_RESTART_REQUIRED_PATHS = {
    "features.tools",
    "features.memory",
    "features.rag",
    "features.tts",
    "paths.embed_model_dir",
    "tts.provider",
    "tts.voice",
    "tts.base_url",
    "tts.ref_audio",
    "tts.prompt_text",
    "tts.engine_dir",
    "tts.temperature",
    "tts.top_k",
    "tts.top_p",
    "tts.ref_audio_dir",
    "tts.timeout_s",
    "tts.rate",
    "tts.volume",
}


class ConfigUpdate(BaseModel):
    """PATCH /config 的请求体

    values：要写入的配置项，键是点分路径（如 "features.tts"）；
    reset：要恢复出厂默认的配置项路径（从 overrides 里删掉该键）
    """

    model_config = ConfigDict(extra="forbid")

    values: dict[str, Any] = Field(default_factory=dict)
    reset: list[str] = Field(default_factory=list)


def _snapshot(settings: Settings) -> dict:
    """非敏感的配置快照，供前端展示 / 渲染设置面板"""

    api_key = settings.model.api_key.get_secret_value()

    return {
        "app": {
            "name": settings.app.name,
            "version": settings.app.version,
            "env": settings.app.env,
        },
        "contracts": {
            "ws_event_version": settings.contracts.ws_event_version,
        },
        "features": {
            "memory": settings.features.memory,
            "rag": settings.features.rag,
            "tools": settings.features.tools,
            "tts": settings.features.tts,
        },
        "model": {
            "provider": settings.model.provider,
            "adapter": settings.model.adapter,
            "model": settings.model.model,
            "base_url": settings.model.base_url,
            "api_key_set": bool(api_key),
        },
        "cost": {
            "budget": settings.cost.budget,
            "currency": settings.cost.currency,
        },
        "tts": {
            "provider": settings.tts.provider,
            "voice": settings.tts.voice,
            "rate": settings.tts.rate,
        },
        "agent": {
            "default_session_id": settings.agent.default_session_id,
            "history_turns": settings.agent.history_turns,
            "max_tool_steps": settings.agent.max_tool_steps,
        },
        "settings": build_settings_payload(settings),
    }


def _get_config_lock(app) -> asyncio.Lock:
    """取 app.state 上的配置锁，没有就创建。"""
    lock = getattr(app.state, "config_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.config_lock = lock
    return lock


async def _rebuild_runtime(app, new_settings: Settings) -> None:
    """
    重建运行时对象：adapter / graph / runner。

    用 app.state.config_lock 串行化，避免并发重建。
    旧的 adapter 异步关闭，进行中的请求用旧对象继续不受影响。
    任一环节失败时，app.state 保持原样。
    """
    lock = _get_config_lock(app)

    async with lock:
        persona_store = getattr(app.state, "persona_store", None)
        if persona_store is None:
            raise RuntimeError(
                "persona_store 未初始化，无法重建 runner"
            )

        old_adapter = getattr(app.state, "model_adapter", None)
        old_settings = getattr(app.state, "settings", None)

        # 1. 构造新 adapter（可能抛，抛了就不动 state）
        new_adapter = build_adapter(new_settings)

        try:
            # 2. 重建图与 runner
            openai_schemas = getattr(app.state, "openai_schemas", [])
            new_graph = build_graph()
            new_runner = AgentRunner(
                graph=new_graph,
                adapter=new_adapter,
                persona_prompt=persona_store.prompt,
                settings=new_settings,
                openai_schemas=openai_schemas,
                persona=persona_store.persona,
            )
        except Exception:
            # 构造 runner 失败，关掉新 adapter，避免连接泄漏
            await new_adapter.aclose()
            raise

        # 3. 原子替换 app.state
        app.state.settings = new_settings
        app.state.model_adapter = new_adapter
        app.state.agent_runner = new_runner

        # 4. embedding 路径变了 → 重置 embedder 单例
        if old_settings is not None:
            old_path = old_settings.paths.embed_model_dir
            new_path = new_settings.paths.embed_model_dir
            if old_path != new_path:
                try:
                    from app.memory.embedder import reset_embedder
                    reset_embedder()
                    logger.info(
                        "嵌入模型路径变更，已重置 embedder 单例"
                    )
                except Exception:
                    logger.exception("重置 embedder 失败，忽略")

        # 5. 关闭旧 adapter（异步，不阻塞响应）
        if old_adapter is not None:
            try:
                await old_adapter.aclose()
            except Exception:
                logger.exception("关闭旧 adapter 失败，忽略")

        logger.info(
            "运行时已重建：model=%s api_key_set=%s",
            new_settings.model.model,
            bool(new_settings.model.api_key.get_secret_value()),
        )


@router.get("/public")
async def public_config(request: Request) -> dict:
    """返回非敏感的配置快照，供前端展示"""

    return _snapshot(request.app.state.settings)


@router.patch("")
async def update_config(request: Request, payload: ConfigUpdate) -> dict:
    """
    写入白名单内的配置项到 config.overrides.yaml，
    并立即重建运行时对象（adapter / graph / runner），无需重启后端。

    少数由 lifespan 装配的键（功能开关、TTS 音色、嵌入模型目录）重建时不会
    重新装配，响应里的 restart_required 会置 True 提醒调用方。

    失败时：
    - apply_patch 失败 → 400，运行时不动
    - load_settings 校验失败 → 400，运行时不动
    - rebuild 失败 → 500，运行时保持原样（新配置已写入文件，
      下次重启后端会生效）
    """

    changed = set(payload.values) | set(payload.reset)

    # 1. 写文件
    try:
        apply_patch(payload.values, payload.reset)
    except ConfigValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        logger.error("写入配置失败：%s", exc)
        raise HTTPException(
            status_code=500, detail=f"写入配置失败：{exc}"
        ) from exc

    logger.info(
        "配置已写入 overrides：%d 项修改，%d 项恢复默认",
        len(payload.values),
        len(payload.reset),
    )

    # 2. 重读磁盘并校验
    try:
        new_settings = load_settings()
    except (SystemExit, Exception) as exc:  # noqa: B014
        logger.warning("写入后配置校验失败：%s", exc)
        raise HTTPException(
            status_code=400,
            detail=f"新配置校验失败：{exc}",
        ) from exc

    # 3. 重建运行时
    try:
        await _rebuild_runtime(request.app, new_settings)
    except Exception as exc:
        logger.exception("重建运行时失败")
        raise HTTPException(
            status_code=500,
            detail=f"配置已写入，但重建运行时失败：{exc}",
        ) from exc

    # 4. 只回显键名，不回显值（values 里可能有 API Key）
    return {
        "ok": True,
        "updated": sorted(payload.values),
        "reset": sorted(payload.reset),
        "restart_required": bool(changed & _RESTART_REQUIRED_PATHS),
        "config": _snapshot(new_settings),
    }
