from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.ws_sender import WsSender
from app.events import make_event, now_iso
from app.logging_config import get_logger
from app.memory.audit import log_event
from app.memory.embedder import get_embedder
from app.permission import get_pending_registry
from app.storage.db_SQlite import execute, insert
from app.storage.session import ensure_session
from app.tts import SentenceSplitter

logger = get_logger("api.ws")

router = APIRouter(tags=["ws"])


# ---------- 错误码 ----------

class WsErrorCode:
    """WebSocket 错误码"""

    BAD_JSON = "bad_json"
    BAD_REQUEST = "bad_request"
    VERSION_MISMATCH = "version_mismatch"
    UNSUPPORTED_TYPE = "unsupported_type"
    INTERNAL_ERROR = "internal_error"
    MODEL_ERROR = "model_error"
    RECURSION_LIMIT = "recursion_limit"
    RUN_TIMEOUT = "run_timeout"
    CANCELLED = "cancelled"
    TOOL_LOOP_LIMIT = "tool_loop_limit"
    MEMORY_UNAVAILABLE = "memory_unavailable"
    BUSY = "busy"


_MESSAGES = {
    WsErrorCode.BAD_JSON: "消息不是合法 JSON",
    WsErrorCode.BAD_REQUEST: "缺少必填字段 v 或 type",
    WsErrorCode.UNSUPPORTED_TYPE: "不支持的消息类型",
    WsErrorCode.INTERNAL_ERROR: "服务端处理消息时出错",
    WsErrorCode.MODEL_ERROR: "模型调用失败",
    WsErrorCode.RECURSION_LIMIT: "思考步数超限",
    WsErrorCode.RUN_TIMEOUT: "思考时间过长",
    WsErrorCode.CANCELLED: "生成被取消",
    WsErrorCode.TOOL_LOOP_LIMIT: "工具调用次数超限",
    WsErrorCode.MEMORY_UNAVAILABLE: "记忆检索不可用",
    WsErrorCode.BUSY: "上一轮还在进行中",
}


def _error_message(code: str, msg: Any, version: int) -> str:
    """按错误码生成对应文案，不复用错误信息"""

    if code == WsErrorCode.VERSION_MISMATCH:
        client_v = msg.get("v") if isinstance(msg, dict) else None
        return f"契约版本不匹配：客户端 {client_v}，服务端 {version}"

    if code == WsErrorCode.UNSUPPORTED_TYPE:
        mtype = msg.get("type") if isinstance(msg, dict) else None
        return f"不支持的消息类型：{mtype}"

    return _MESSAGES.get(code, "未知错误")


# ---------- 工具 ----------

def _new_id() -> str:
    return uuid4().hex


def _estimate_cost(settings, model: str, tokens_in: int, tokens_out: int) -> float:
    """按模型估算成本（CNY） TODO: 从配置读取价格表"""

    price = settings.cost.prices.get(model)
    if price is None:
        return 0.0
    return tokens_in / 1000 * price.input_per_1k + tokens_out / 1000 * price.output_per_1k


# ---------- 发送 ----------

async def send_event(
    sender: WsSender,
    type_: str,
    *,
    version: int,
    request_id: str | None = None,
    session_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """发送一个事件，WsSender 内部加锁"""

    await sender.send_json(
        make_event(
            type_,
            version=version,
            request_id=request_id,
            session_id=session_id,
            data=data,
        )
    )


async def send_error(
    sender: WsSender,
    code: str,
    message: str,
    *,
    version: int,
    request_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """发送一个错误事件。"""
    await send_event(
        sender,
        "error",
        version=version,
        request_id=request_id,
        session_id=session_id,
        data={"code": code, "message": message},
    )


# ---------- 校验 ----------

def _validate_envelope(
    msg: Any,
    *,
    expected_version: int,
) -> tuple[bool, str | None, str | None]:
    """客户端消息信封校验，返回 (ok, error_code, request_id)"""

    if not isinstance(msg, dict):
        return False, WsErrorCode.BAD_JSON, None

    request_id = msg.get("request_id")
    if not isinstance(request_id, str) or request_id.strip() == "":
        request_id = None

    if "v" not in msg or "type" not in msg:
        return False, WsErrorCode.BAD_REQUEST, request_id

    # 严格比较，防止 True == 1 绕过
    v = msg.get("v")
    if not isinstance(v, int) or isinstance(v, bool) or v != expected_version:
        return False, WsErrorCode.VERSION_MISMATCH, request_id

    return True, None, request_id


# ---------- TTS ----------

# 至少一个可发音字符（字母 / 数字 / 汉字），纯标点如 "……" 不送合成
_SPEAKABLE_RE = re.compile(r"[^\W_]")


class _TtsStream:
    """
    一轮对话的 TTS 流式合成。

    feed() 把模型增量喂给切句器，后台 worker 串行合成并推送 tts_audio_chunk；
    finish() 冲刷剩余文本、等 worker 收尾，返回 (chunks, chars)
    """

    def __init__(
        self,
        sender: WsSender,
        settings,
        adapter,
        *,
        version: int,
        request_id: str | None,
        session_id: str,
    ) -> None:
        self.sender = sender
        self.settings = settings
        self.adapter = adapter
        self.version = version
        self.request_id = request_id
        self.session_id = session_id

        # 本轮的情绪（emotion 事件一定排在第一个 text_delta 之前），
        # 按情绪换参考音频的适配器要用
        self.emotion: str | None = None

        self.enabled = bool(settings.features.tts and adapter is not None)
        self._splitter = SentenceSplitter(settings.tts)
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def set_emotion(self, emotion: str | None) -> None:
        """记下本轮情绪；按情绪换参考音频的适配器据此挑素材"""

        self.emotion = emotion

    def start(self, lifecycle) -> None:
        """注册后台 worker，与主流程并发跑"""

        if not self.enabled:
            return
        self._task = lifecycle.register_task(self._worker())

    async def feed(self, text: str) -> None:
        """喂入模型增量，凑成整句就入队"""

        if not self.enabled or not text:
            return
        for sentence in self._splitter.feed(text):
            await self._queue.put(sentence)

    async def finish(self) -> tuple[int, int]:
        """冲刷剩余文本并等 worker 结束，返回 (chunks, chars)"""

        if not self.enabled or self._task is None:
            return 0, 0

        for sentence in self._splitter.flush():
            await self._queue.put(sentence)
        await self._queue.put(None)

        try:
            return await self._task
        except Exception:
            logger.exception("TTS worker 异常：request_id=%s", self.request_id)
            return 0, 0

    async def _worker(self) -> tuple[int, int]:
        """串行消费句子：合成 → 推送音频；返回 (chunks, chars)"""

        limit = self.settings.tts.max_chars_per_turn
        index = 0
        total = 0

        while True:
            sentence = await self._queue.get()
            if sentence is None:
                break

            if not _SPEAKABLE_RE.search(sentence):
                continue

            try:
                result = await self.adapter.synthesize(sentence, emotion=self.emotion)
            except Exception:
                logger.exception("TTS 合成失败：%r", sentence[:40])
                continue

            if not result.audio:
                continue

            if total + result.chars > limit:
                logger.info(
                    "TTS 字符数超上限，停止合成：request_id=%s total=%d limit=%d",
                    self.request_id, total, limit,
                )
                break

            await send_event(
                self.sender,
                "tts_audio_chunk",
                version=self.version,
                request_id=self.request_id,
                session_id=self.session_id,
                data={
                    "audio": base64.b64encode(result.audio).decode("ascii"),
                    "format": result.format,
                    "text": sentence,
                    "index": index,
                },
            )
            index += 1
            total += result.chars

        return index, total


async def _update_tts_chars(settings, message_id: str, chars: int) -> None:
    """把本轮 TTS 字符数补进 cost_log，失败不影响主流程"""

    try:
        await execute(
            settings,
            "UPDATE cost_log SET tts_chars = ? WHERE message_id = ?",
            (chars, message_id),
        )
    except Exception:
        logger.exception("更新 TTS 字符数失败：message_id=%s", message_id)


# ---------- text_input ----------

async def _handle_text_input(
    sender: WsSender,
    msg: dict[str, Any],
    *,
    version: int,
) -> None:
    """处理 text_input：调 Agent，流式返回，落库"""

    app_state = sender.app_state
    settings = app_state.settings
    lifecycle = app_state.lifecycle
    runner = app_state.agent_runner

    req_id = msg.get("request_id")

    # 单用户单会话：固定 session_id
    session_id = settings.agent.default_session_id

    client_session_id = msg.get("session_id")
    if client_session_id is not None and client_session_id != session_id:
        await send_error(
            sender,
            WsErrorCode.BAD_REQUEST,
            f"本应用只支持单一会话：{session_id}",
            version=version,
            request_id=req_id,
            session_id=session_id,
        )
        return

    # data 与 text 校验
    data = msg.get("data")
    if data is None:
        data = {}
    if not isinstance(data, dict):
        await send_error(
            sender,
            WsErrorCode.BAD_REQUEST,
            "data 必须是对象",
            version=version,
            request_id=req_id,
            session_id=session_id,
        )
        return

    raw_text = data.get("text")
    if not isinstance(raw_text, str):
        await send_error(
            sender,
            WsErrorCode.BAD_REQUEST,
            "data.text 必须是字符串",
            version=version,
            request_id=req_id,
            session_id=session_id,
        )
        return

    user_text = raw_text.strip()
    if not user_text:
        await send_error(
            sender,
            WsErrorCode.BAD_REQUEST,
            "text 不能为空",
            version=version,
            request_id=req_id,
            session_id=session_id,
        )
        return

    if lifecycle.is_shutting_down:
        await send_error(
            sender,
            WsErrorCode.INTERNAL_ERROR,
            "服务正在关闭，暂不接受新请求",
            version=version,
            request_id=req_id,
            session_id=session_id,
        )
        return

    # 记忆/知识召回降级提示：嵌入模型没就绪时本轮不带记忆，但对话照常进行，
    # 每条连接只提示一次，避免每轮都刷一条错误气泡
    if settings.features.memory or settings.features.rag:
        embedder = get_embedder(settings)
        if embedder.unavailable_reason and not sender.memory_unavailable_notified:
            sender.memory_unavailable_notified = True
            await send_error(
                sender,
                WsErrorCode.MEMORY_UNAVAILABLE,
                f"记忆检索不可用（{embedder.unavailable_reason}），本轮回复不会使用历史记忆",
                version=version,
                request_id=req_id,
                session_id=session_id,
            )

    # 落库：刷新会话 updated_at + 用户消息
    await ensure_session(settings, session_id)
    user_msg_id = _new_id()
    await insert(
        settings,
        "INSERT INTO messages(id, session_id, role, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_msg_id, session_id, "user", user_text, now_iso()),
    )

    model_name = settings.model.model
    tokens_in = 0
    tokens_out = 0
    full_text = ""
    asst_id: str | None = None

    pending_registry = get_pending_registry()

    tts = _TtsStream(
        sender,
        settings,
        getattr(app_state, "tts_adapter", None),
        version=version,
        request_id=req_id,
        session_id=session_id,
    )
    tts.start(lifecycle)

    # 工具调用的元信息与结果状态：落库 tool 行 + 写审计时用
    tool_status: dict[str, str] = {}
    tool_meta: dict[str, dict] = {}

    async def on_tool_event(event_type: str, data: dict) -> None:
        """工具事件转发给前端；同时记下元信息/结果状态，并写一条审计"""
        tool_call_id = data.get("tool_call_id")

        if tool_call_id and event_type == "tool_call_started":
            tool_meta[tool_call_id] = {
                "tool": data.get("tool"),
                "args": data.get("args") or {},
            }

        await send_event(
            sender,
            event_type,
            version=version,
            request_id=req_id,
            session_id=session_id,
            data=data,
        )

        if not tool_call_id:
            return

        if event_type == "tool_result":
            tool_status[tool_call_id] = "ok" if data.get("ok") else "error"
        elif event_type == "tool_denied":
            tool_status[tool_call_id] = "denied"
        else:
            return

        # 写审计：谁在什么时候动了哪个工具、参数是什么、结果如何。
        # 只记参数与结果摘要，不存工具返回的正文（正文在 messages 表里）
        meta = tool_meta.get(tool_call_id) or {}
        status = tool_status[tool_call_id]
        await log_event(
            settings,
            event_type="tool_call",
            actor="assistant",
            target_type="tool",
            target_id=tool_call_id,
            action=str(meta.get("tool") or data.get("tool") or ""),
            before={"args": meta.get("args") or {}},
            after={
                "status": status,
                "error": data.get("error"),
                "chars": len(data.get("content") or ""),
            },
            status=status,
        )

    try:
        async for event in runner.run(
            user_text,
            should_stop=lambda: lifecycle.is_shutting_down,
            request_id=req_id,
            session_id=session_id,
            exclude_message_id=user_msg_id,
            on_tool_event=on_tool_event,
            pending_registry=pending_registry,
        ):
            if event.type == "text_delta":
                await send_event(
                    sender,
                    "text_delta",
                    version=version,
                    request_id=req_id,
                    session_id=session_id,
                    data=event.data,
                )
                # 模型增量喂给切句器，凑成整句就交给后台合成
                await tts.feed(event.data.get("text", ""))
                continue

            if event.type == "emotion":
                # 记下来给 TTS 用（按情绪换参考音频）
                tts.set_emotion(event.data.get("emotion"))
                await send_event(
                    sender,
                    "emotion",
                    version=version,
                    request_id=req_id,
                    session_id=session_id,
                    data=event.data,
                )
                continue

            if event.type == "expert_selected":
                await send_event(
                    sender,
                    "expert_selected",
                    version=version,
                    request_id=req_id,
                    session_id=session_id,
                    data=event.data,
                )
                continue

            if event.type == "run_finished":
                tokens_in = event.data.get("tokens_in") or 0
                tokens_out = event.data.get("tokens_out") or 0
                full_text = event.data.get("text") or ""
                model_name = event.data.get("model") or model_name

                msg_sequence = event.data.get("messages") or []
                # runner 没给消息序列（例如测试用的假 runner）时退回"只落最终回复"，
                # 否则这一轮会一条消息都不落库
                if not msg_sequence:
                    msg_sequence = [
                        {"role": "assistant", "content": full_text}
                    ]

                # token 只记在最后一条 assistant（最终回复）上
                last_asst_idx = max(
                    (
                        idx
                        for idx, m in enumerate(msg_sequence)
                        if m.get("role") == "assistant"
                    ),
                    default=None,
                )

                for idx, m in enumerate(msg_sequence):
                    role = m.get("role")
                    if role == "assistant":
                        mid = _new_id()
                        tool_calls = m.get("tool_calls") or None
                        is_last = idx == last_asst_idx
                        await insert(
                            settings,
                            "INSERT INTO messages("
                            "id, session_id, role, content, created_at, "
                            "tool_calls, tokens_in, tokens_out, model"
                            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                mid,
                                session_id,
                                "assistant",
                                m.get("content") or "",
                                now_iso(),
                                json.dumps(tool_calls, ensure_ascii=False)
                                if tool_calls
                                else None,
                                tokens_in if is_last else 0,
                                tokens_out if is_last else 0,
                                model_name,
                            ),
                        )
                        asst_id = mid
                    elif role == "tool":
                        tool_call_id = m.get("tool_call_id")
                        await insert(
                            settings,
                            "INSERT INTO messages("
                            "id, session_id, role, content, created_at, "
                            "tool_call_id, name, status"
                            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                _new_id(),
                                session_id,
                                "tool",
                                m.get("content") or "",
                                now_iso(),
                                tool_call_id,
                                m.get("name"),
                                tool_status.get(tool_call_id or ""),
                            ),
                        )

                cost = _estimate_cost(settings, model_name, tokens_in, tokens_out)
                await insert(
                    settings,
                    "INSERT INTO cost_log("
                    "session_id, message_id, provider, model, "
                    "tokens_in, tokens_out, estimated_cost, currency, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        session_id,
                        asst_id,
                        settings.model.provider,
                        model_name,
                        tokens_in,
                        tokens_out,
                        cost,
                        settings.cost.currency,
                        now_iso(),
                    ),
                )

                # ---------- 后台任务 ----------
                adapter = getattr(runner, "adapter", None)

                if adapter is not None:
                    # 摘要
                    if settings.agent.summary_trigger_tokens > 0:
                        from app.storage.history import maybe_summarize

                        lifecycle.register_task(
                            maybe_summarize(
                                settings,
                                session_id,
                                adapter,
                            )
                        )

                    # 记忆抽取
                    if settings.features.memory and settings.memory.extract_per_turn:
                        from app.memory.extract import extract_and_store

                        lifecycle.register_task(
                            extract_and_store(
                                settings,
                                adapter,
                                user_text,
                                full_text,
                            )
                        )

                await send_event(
                    sender,
                    "run_finished",
                    version=version,
                    request_id=req_id,
                    session_id=session_id,
                    data={
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "model": model_name,
                        "estimated_cost": cost,
                        "currency": settings.cost.currency,
                    },
                )
                continue

            if event.type == "error":
                code = event.data.get("code", WsErrorCode.MODEL_ERROR)
                await send_error(
                    sender,
                    code,
                    event.data.get("message")
                    or _error_message(code, None, version),
                    version=version,
                    request_id=req_id,
                    session_id=session_id,
                )
                break
    finally:
        # 成功或失败都要收尾，避免 TTS worker 挂在队列上
        tts_chunks, tts_chars = await tts.finish()

    if tts_chunks > 0:
        # 先记账再发 tts_finished：前端收到它即代表本轮 TTS 全部结束
        if asst_id is not None:
            await _update_tts_chars(settings, asst_id, tts_chars)

        await send_event(
            sender,
            "tts_finished",
            version=version,
            request_id=req_id,
            session_id=session_id,
            data={"chunks": tts_chunks, "chars": tts_chars},
        )
        logger.info(
            "TTS 本轮完成：request_id=%s chunks=%d chars=%d",
            req_id, tts_chunks, tts_chars,
        )


async def _handle_tool_confirm(
    sender: WsSender,
    msg: dict[str, Any],
    *,
    version: int,
) -> None:
    """
    前端回复工具确认

    消息格式：
        {
          "v": 1,
          "type": "tool_confirm",
          "request_id": "r2-c",
          "data": {"confirm_id": "r2:call_xxx", "allowed": true}
        }
    """

    req_id = msg.get("request_id")
    data = msg.get("data") or {}

    if not isinstance(data, dict):
        await send_error(
            sender,
            WsErrorCode.BAD_REQUEST,
            "data 必须是对象",
            version=version,
            request_id=req_id,
        )
        return

    confirm_id = data.get("confirm_id")
    allowed = data.get("allowed")

    if not isinstance(confirm_id, str) or not confirm_id.strip():
        await send_error(
            sender,
            WsErrorCode.BAD_REQUEST,
            "data.confirm_id 必须是非空字符串",
            version=version,
            request_id=req_id,
        )
        return

    if not isinstance(allowed, bool):
        await send_error(
            sender,
            WsErrorCode.BAD_REQUEST,
            "data.allowed 必须是布尔值",
            version=version,
            request_id=req_id,
        )
        return

    registry = get_pending_registry()
    ok = await registry.resolve(confirm_id, allowed)

    if not ok:
        await send_error(
            sender,
            WsErrorCode.BAD_REQUEST,
            f"确认 ID 不存在或已超时：{confirm_id}",
            version=version,
            request_id=req_id,
        )
        return

    logger.info("工具确认已处理：confirm_id=%s allowed=%s", confirm_id, allowed)


# ---------- 分发 ----------

async def handle_message(
    sender: WsSender,
    msg: dict[str, Any],
    *,
    version: int,
) -> None:
    """根据 type 处理消息"""

    mtype = msg.get("type")
    request_id = msg.get("request_id")
    session_id = msg.get("session_id")
    data = msg.get("data") or {}

    if mtype == "ping":
        await send_event(
            sender,
            "pong",
            version=version,
            request_id=request_id,
            session_id=session_id,
            data={"server_time": now_iso()},
        )
        return

    if mtype == "echo":
        await send_event(
            sender,
            "echo",
            version=version,
            request_id=request_id,
            session_id=session_id,
            data=data,
        )
        return

    if mtype == "text_input":
        # 不能内联 await：整轮 agent 执行会占住接收循环，期间
        # tool_confirm / ping / 停机信号都读不到，工具确认必然超时。
        current = sender.turn_task
        if current is not None and not current.done():
            await send_error(
                sender,
                WsErrorCode.BUSY,
                _error_message(WsErrorCode.BUSY, msg, version),
                version=version,
                request_id=request_id,
                session_id=session_id,
            )
            return

        task = asyncio.create_task(
            _handle_text_input(sender, msg, version=version),
            name="agent-turn",
        )
        sender.turn_task = task
        task.add_done_callback(_on_turn_done)
        return

    if mtype == "tool_confirm":
        await _handle_tool_confirm(sender, msg, version=version)
        return

    await send_error(
        sender,
        WsErrorCode.UNSUPPORTED_TYPE,
        _error_message(WsErrorCode.UNSUPPORTED_TYPE, msg, version),
        version=version,
        request_id=request_id,
        session_id=session_id,
    )


# ---------- 清理 ----------

async def _cancel_and_drain(task: asyncio.Task) -> None:
    """取消任务并消费其异常，避免 'Task exception was never retrieved'。"""
    if task.done():
        try:
            task.exception()
        except BaseException:
            pass
        return
    task.cancel()
    try:
        await task
    except BaseException:
        pass


def _on_turn_done(task: asyncio.Task) -> None:
    """后台轮次结束：消费异常，避免 'Task exception was never retrieved'"""

    if task.cancelled():
        return

    exc = task.exception()
    if exc is not None:
        logger.error("本轮执行异常", exc_info=exc)


# ---------- 端点 ----------

@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """WebSocket 端点"""

    settings = websocket.app.state.settings
    lifecycle = websocket.app.state.lifecycle
    version = settings.contracts.ws_event_version

    await websocket.accept()

    sender = WsSender(websocket)
    lifecycle.register_ws(sender)
    logger.info("WebSocket 已连接：%s", websocket.client)

    shutdown_task = asyncio.create_task(lifecycle.wait_for_shutdown())
    recv_task: asyncio.Task[str] | None = None

    try:
        while True:
            recv_task = asyncio.create_task(websocket.receive_text())

            done, _ = await asyncio.wait(
                {recv_task, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            if shutdown_task in done:
                await _cancel_and_drain(recv_task)
                logger.info("检测到停机信号，主动断开：%s", websocket.client)
                return

            raw = recv_task.result()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await send_error(
                    sender,
                    WsErrorCode.BAD_JSON,
                    _error_message(WsErrorCode.BAD_JSON, None, version),
                    version=version,
                )
                continue

            ok, err_code, req_id = _validate_envelope(
                msg,
                expected_version=version,
            )
            if not ok:
                await send_error(
                    sender,
                    err_code or WsErrorCode.BAD_REQUEST,
                    _error_message(err_code or WsErrorCode.BAD_REQUEST, msg, version),
                    version=version,
                    request_id=req_id,
                )
                continue

            try:
                await handle_message(sender, msg, version=version)
            except Exception:
                logger.exception("WebSocket 消息处理异常")
                await send_error(
                    sender,
                    WsErrorCode.INTERNAL_ERROR,
                    _error_message(WsErrorCode.INTERNAL_ERROR, msg, version),
                    version=version,
                    request_id=req_id,
                )

    except WebSocketDisconnect:
        logger.info("WebSocket 已断开：%s", websocket.client)
    except Exception:
        logger.exception("WebSocket 未预期异常")
    finally:
        if recv_task is not None:
            await _cancel_and_drain(recv_task)
        # 连接断开时把还在跑的那一轮一起收掉，别留孤儿任务继续往死连接上写
        turn_task = sender.turn_task
        if turn_task is not None:
            await _cancel_and_drain(turn_task)
        await _cancel_and_drain(shutdown_task)
        lifecycle.unregister_ws(sender)
        await sender.close(code=1001)
