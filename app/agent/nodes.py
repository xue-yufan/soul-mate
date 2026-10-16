from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig

from app.agent.emotion import (
    DEFAULT_EMOTION,
    MAX_PREFIX,
    is_emotion_prefix,
    parse_emotion_tag,
    salvage_emotion_prefix,
)
from app.agent.state import AgentState
from app.logging_config import get_logger
from app.models.base import ChatMessage, ToolCall
from app.persona.confirm import render_confirm_prompt, render_declined

logger = get_logger(__name__)

# 意图分类是主回复之前的一次额外模型调用，给它单独设上限，
# 免得分类模型卡住把整轮 run_timeout_s（默认 120s）吃光
_INTENT_TIMEOUT_S = 10.0


def _lc_to_chat(m: BaseMessage) -> ChatMessage:
    """
    LangChain 消息转适配层消息

    角色映射：
    - SystemMessage  → system
    - AIMessage      → assistant
    - HumanMessage   → user
    - ToolMessage    → tool
    - 其他           → user（兜底，不应出现）

    内容映射：
    - str           → 直接使用
    - list[dict]    → 多模态，暂不支持，抛 NotImplementedError
    - 其他          → str() 兜底，不应出现
    """

    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    if isinstance(m, SystemMessage):
        role = "system"
    elif isinstance(m, AIMessage):
        role = "assistant"
        if getattr(m, "tool_calls", None):
            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=json.dumps(tc["args"], ensure_ascii=False),
                )
                for tc in m.tool_calls
            ]
    elif isinstance(m, HumanMessage):
        role = "user"
    elif isinstance(m, ToolMessage):
        role = "tool"
        tool_call_id = getattr(m, "tool_call_id", None)
    else:
        role = "user"

    content = m.content
    if isinstance(content, str):
        pass
    elif isinstance(content, list):
        raise NotImplementedError("多模态消息暂未支持")
    else:
        content = str(content)

    return ChatMessage(
        role=role,
        content=content,
        tool_call_id=tool_call_id,
        tool_calls=tool_calls,
    )


def _in_flight_tool_messages(state: AgentState) -> list[ChatMessage]:
    """
    取本轮图内已经发生的工具往返（助手带 tool_calls 的消息 + 工具结果）

    这些消息只活在 state 里、没有落库，而 chat_node 在库模式下是从数据库历史
    重建上下文的——必须把它们显式补回去，否则模型看不到工具已经执行过，
    会一遍遍重复调用同一个工具，直到撞上工具步数/递归上限

    HumanMessage 不取：本轮用户输入由调用方单独补，避免重复
    """

    out: list[ChatMessage] = []
    for m in state.get("messages", []):
        if isinstance(m, AIMessage):
            if getattr(m, "tool_calls", None):
                out.append(_lc_to_chat(m))
        elif isinstance(m, ToolMessage):
            out.append(_lc_to_chat(m))
    return out


async def supervisor_node(
    state: AgentState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """主管节点：决定下一步，进行意图分类"""

    cfg = config.get("configurable", {})
    should_stop = cfg.get("should_stop")

    if should_stop is not None and should_stop():
        return {"next_step": "end", "error": "cancelled"}

    text = (state.get("user_input") or "").strip()
    if not text:
        return {"next_step": "end"}

    # 意图分类
    adapter = cfg.get("adapter")
    request_id = cfg.get("request_id")

    if adapter is None:
        # 无 adapter，回退 chat
        logger.warning(
            "supervisor 缺少 adapter，回退 chat request_id=%s",
            request_id,
        )
        return {"next_step": "chat", "intent": "chat"}

    from app.agent.intent import classify_intent, record_timeout

    try:
        intent = await asyncio.wait_for(
            classify_intent(adapter, text, request_id=request_id),
            timeout=_INTENT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        record_timeout()
        logger.warning(
            "意图分类超时，回退 chat request_id=%s", request_id
        )
        intent = "chat"

    logger.info(
        "supervisor 路由：intent=%s next_step=%s request_id=%s",
        intent, intent, request_id,
    )
    return {"next_step": intent, "intent": intent}


async def _run_expert(
    state: AgentState,
    config: RunnableConfig,
    *,
    expert: str,
    system_prompt: str,
    use_tools: bool = True,
) -> dict[str, Any]:
    """
    专家节点的公共实现：组 prompt、调模型流式生成、解析情绪、收集工具调用。

    从 config["configurable"] 读取：
    - adapter：模型适配器
    - on_token：async 回调，推送每个 delta
    - on_emotion：async 回调，推送情绪标签
    - on_expert_selected：async 回调，节点入口宣告本轮专家（前端据此显示
      「正在查询信息…」这类等待提示）
    - should_stop：停机检查函数
    - model_name：当前模型名
    - settings：Settings，用于加载历史、摘要、记忆
    - session_id：会话 ID
    - exclude_message_id：本轮 user 消息的落库 id
    - request_id：请求 ID，仅用于日志
    - openai_schemas：OpenAI 兼容模式下的工具 schema

    参数：
    - expert：专家名，写日志，并作为 current_expert 供 tool 之后回跳
    - system_prompt：人格 + 职责 + 情绪格式，由 app.agent.prompts 构造
    - use_tools：该专家是否允许调工具
    """
    cfg = config.get("configurable", {})
    adapter = cfg.get("adapter")
    on_token = cfg.get("on_token")
    on_emotion = cfg.get("on_emotion")
    on_expert_selected = cfg.get("on_expert_selected")
    should_stop = cfg.get("should_stop")
    model_name = cfg.get("model_name") or ""
    request_id = cfg.get("request_id")
    session_id = cfg.get("session_id")
    settings = cfg.get("settings")
    exclude_message_id = cfg.get("exclude_message_id")
    openai_schemas = cfg.get("openai_schemas") or []

    # 入口检查：已经停机就直接返回
    if should_stop is not None and should_stop():
        logger.info(
            "expert=%s 入口检测到停机，跳过 request_id=%s",
            expert, request_id,
        )
        return {
            "messages": [],
            "tokens_in": 0,
            "tokens_out": 0,
            "error": "cancelled",
        }

    if adapter is None:
        raise RuntimeError(f"{expert} 缺少 adapter")

    # 宣告本轮由哪个专家接手。放在真正干活之前发，前端才能尽快亮出等待提示；
    # info 走「工具 → 回 info」时本节点会再次进入，因此同一轮可能发两次。
    if on_expert_selected is not None:
        await on_expert_selected(expert)

    # ---------- 构造消息 ----------

    messages: list[ChatMessage] = []

    # 1. system：人格 + 职责 + 情绪格式（调用方已拼好）+ 摘要 + 记忆
    system_parts: list[str] = []
    if system_prompt:
        system_parts.append(system_prompt)

    history_loaded = False
    summary_loaded = False
    memory_count = 0

    if settings is not None and session_id:
        # 延迟 import，避免模块级循环依赖
        from app.storage.history import (
            load_recent_messages,
            load_summary,
        )

        # 1.1 摘要
        summary = await load_summary(settings, session_id)
        if summary:
            system_parts.append(f"【对话摘要】\n{summary}")
            summary_loaded = True

        # 1.2 记忆 + 设定 + 文档 合并召回
        if settings.features.memory or settings.features.rag:
            try:
                from app.memory.retrieve import (
                    format_items_for_prompt,
                    recall_all,
                )

                query = state.get("user_input") or ""
                if query.strip():
                    items = await recall_all(settings, query)
                    if items:
                        system_parts.append(
                            format_items_for_prompt(items)
                        )
                        memory_count = len(items)
            except Exception:
                logger.exception(
                    "记忆/知识召回失败，跳过 request_id=%s", request_id
                )
                memory_count = 0

        # 1.3 历史
        history = await load_recent_messages(
            settings,
            session_id,
            max_turns=settings.agent.history_turns,
            exclude_message_id=exclude_message_id,
        )
        history_loaded = True
    else:
        history = []

    if system_parts:
        messages.append(
            ChatMessage(
                role="system",
                content="\n\n".join(system_parts),
            )
        )

    # 2. 历史 + 当前 user
    if history_loaded:
        messages.extend(history)

        current_user = state.get("user_input")
        if exclude_message_id and current_user:
            messages.append(
                ChatMessage(role="user", content=current_user)
            )

        messages.extend(_in_flight_tool_messages(state))
    else:
        for m in state.get("messages", []):
            messages.append(_lc_to_chat(m))

    # ---------- 工具决策 ----------
    tool_steps = state.get("tool_steps", 0)
    max_steps = settings.agent.max_tool_steps if settings else 0
    tools_on = use_tools and bool(openai_schemas) and tool_steps < max_steps
    tools_param = openai_schemas if tools_on else None

    # ---------- 调模型 ----------

    text_parts: list[str] = []
    tool_calls_collected: list[ToolCall] = []
    tokens_in = 0
    tokens_out = 0
    chunks = 0
    interrupted = False

    # 情绪缓冲
    emotion_buffer = ""
    emotion_parsed = False
    detected_emotion: str | None = None

    async def flush_buffer_as_text() -> None:
        """把缓冲内容当正文输出，标记情绪解析结束

        模型偶尔把标签写坏（</emom>、漏闭合、大小写乱来），严格正则认不出来时
        这里再抢救一次：认出 <emotion>xxx 就取 xxx 当情绪、把标签从正文里摘掉。
        不抢救的话标签原文会被当正文发出去，而且情绪会退回兜底的 neutral。
        """

        nonlocal emotion_buffer, emotion_parsed, detected_emotion

        if not emotion_buffer:
            emotion_parsed = True
            return

        rest = emotion_buffer
        if detected_emotion is None:
            emo, consumed = salvage_emotion_prefix(rest)
            if emo is not None:
                detected_emotion = emo
                logger.warning(
                    "情绪标签格式异常，已按 %s 救回：%r request_id=%s",
                    emo,
                    rest[:consumed],
                    request_id,
                )
                await emit_emotion(emo)
                rest = rest[consumed:]

        emotion_buffer = ""
        emotion_parsed = True

        if rest:
            text_parts.append(rest)
            if on_token is not None:
                await on_token(rest)

    async def emit_emotion(value: str) -> None:
        """发送情绪事件"""

        if on_emotion is not None:
            await on_emotion(value)

    # 召回来源开关写进日志
    recall_src = "off"
    if settings is not None:
        enabled = [
            name
            for name, on in (
                ("memory", settings.features.memory),
                ("rag", settings.features.rag),
            )
            if on
        ]
        recall_src = "+".join(enabled) if enabled else "off"

    logger.info(
        "expert=%s 开始 request_id=%s session=%s model=%s "
        "messages=%d history=%d summary=%s recall=%d recall_src=%s "
        "tool_steps=%d use_tools=%s",
        expert, request_id, session_id, model_name, len(messages),
        len(history), summary_loaded, memory_count, recall_src,
        tool_steps, tools_on,
    )

    async for chunk in adapter.chat_stream(
        messages,
        tools=tools_param,
    ):
        chunks += 1

        if should_stop is not None and should_stop():
            interrupted = True
            logger.info(
                "expert=%s 检测到停机，中断模型流 request_id=%s chunks=%d",
                expert, request_id, chunks,
            )
            break

        # ----- 文本 + 情绪解析 -----

        if chunk.delta:
            if emotion_parsed:
                # 情绪已解析，正文直通
                text_parts.append(chunk.delta)
                if on_token is not None:
                    await on_token(chunk.delta)
            else:
                # 只有「还可能是开头标签的一部分」时才值得缓冲；
                # 一旦确定不可能是标签，立刻当正文直通，避免正文被扣住不流式
                emotion_buffer += chunk.delta
                emo, consumed = parse_emotion_tag(emotion_buffer)

                if emo is not None:
                    # 匹配成功
                    detected_emotion = emo
                    rest = emotion_buffer[consumed:]
                    emotion_buffer = ""
                    emotion_parsed = True

                    await emit_emotion(emo)

                    if rest:
                        text_parts.append(rest)
                        if on_token is not None:
                            await on_token(rest)
                elif (
                    len(emotion_buffer) <= MAX_PREFIX
                    and is_emotion_prefix(emotion_buffer)
                ):
                    # 仍可能是标签前缀，继续等后续 chunk
                    pass
                else:
                    # 超长或确定不是标签前缀，当无标签处理
                    logger.debug(
                        "开头不是情绪标签，按无标签处理 "
                        "request_id=%s",
                        request_id,
                    )
                    await flush_buffer_as_text()

        # ----- 工具调用 -----

        if chunk.tool_calls:
            # 工具调用前把情绪缓冲冲掉（如果有）
            if not emotion_parsed:
                await flush_buffer_as_text()
            tool_calls_collected.extend(chunk.tool_calls)

        # ----- token 统计 -----

        if chunk.tokens_in:
            tokens_in = chunk.tokens_in
        if chunk.tokens_out:
            tokens_out = chunk.tokens_out

    # 流结束：缓冲还有内容说明没解析到标签
    if not emotion_parsed and emotion_buffer:
        logger.debug(
            "流结束仍未匹配情绪标签，按无标签处理 request_id=%s",
            request_id,
        )
        await flush_buffer_as_text()

    # 未识别情绪，发一个 neutral 兜底
    if detected_emotion is None:
        await emit_emotion(DEFAULT_EMOTION)

    full_text = "".join(text_parts)

    # 构造 AIMessage，带 tool_calls（如模型请求）
    lc_tool_calls: list[dict[str, Any]] = []
    for tc in tool_calls_collected:
        try:
            args = json.loads(tc.arguments) if tc.arguments else {}
        except json.JSONDecodeError:
            logger.warning(
                "工具参数非法 JSON：tool=%s args=%r",
                tc.name, tc.arguments[:200],
            )
            args = {}
        lc_tool_calls.append({"id": tc.id, "name": tc.name, "args": args})

    ai_msg = AIMessage(
        content=full_text,
        tool_calls=lc_tool_calls,
    )

    logger.info(
        "expert=%s 结束 request_id=%s chunks=%d interrupted=%s "
        "tokens_in=%s tokens_out=%s 回复chars=%d tool_calls=%d "
        "emotion=%s",
        expert, request_id, chunks, interrupted, tokens_in, tokens_out,
        len(full_text), len(lc_tool_calls), detected_emotion,
    )

    return {
        "messages": [ai_msg],
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": model_name,
        # tool 之后据此回到发起工具的专家（info 接工具时才真正用上）
        "current_expert": expert,
    }


# ---------- 专家节点壳 ----------


async def chat_node(
    state: AgentState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """闲聊专家：人格 + 情绪格式，不带工具。"""

    cfg = config.get("configurable", {})
    persona_prompt = cfg.get("persona_prompt") or ""

    from app.agent.prompts import build_chat_prompt

    return await _run_expert(
        state,
        config,
        expert="chat",
        system_prompt=build_chat_prompt(persona_prompt),
        use_tools=False,
    )


async def info_node(
    state: AgentState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """
    信息查询专家：人格 + 信息查询职责 + 情绪格式。

    带工具：时间/天气这类问题要靠工具拿实时信息，白名单与确认策略在
    permission 里配；工具跑完由 graph 按 current_expert 路由回本节点继续作答。
    """

    cfg = config.get("configurable", {})
    persona_prompt = cfg.get("persona_prompt") or ""

    from app.agent.prompts import build_info_prompt

    return await _run_expert(
        state,
        config,
        expert="info",
        system_prompt=build_info_prompt(persona_prompt),
        use_tools=True,
    )


# ---------- tool_node ----------

async def tool_node(
    state: AgentState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """
    工具节点：执行上一条 AIMessage 请求的工具调用。

    执行策略：
    - 权限检查串行（confirm 是交互式的，不能并发）
    - 通过权限的工具并行执行（gather）
    - 结果按原顺序收集和发事件，前端展示不乱

    从 config["configurable"] 读取：
    - settings / session_id / request_id
    - on_tool_event：async (event_type, data) -> None，发工具事件
    - pending_registry：confirm 交互用
    - persona：渲染确认语用
    - on_token：拒绝后说收手话用
    """

    cfg = config.get("configurable", {})
    settings = cfg.get("settings")
    session_id = cfg.get("session_id") or ""
    request_id = cfg.get("request_id") or ""
    on_tool_event = cfg.get("on_tool_event")
    pending_registry = cfg.get("pending_registry")
    persona = cfg.get("persona")
    on_token = cfg.get("on_token")

    if settings is None:
        raise RuntimeError("tool_node 缺少 settings")

    # 找最近一条带 tool_calls 的 AIMessage
    last_ai: AIMessage | None = None
    for m in reversed(state.get("messages", [])):
        if isinstance(m, AIMessage):
            last_ai = m
            break

    if last_ai is None or not last_ai.tool_calls:
        return {}

    tool_steps = state.get("tool_steps", 0)
    if tool_steps >= settings.agent.max_tool_steps:
        logger.warning(
            "工具步数超限 request_id=%s steps=%d",
            request_id, tool_steps,
        )
        return {"error": "tool_loop_limit"}

    from app.permission import Decision
    from app.permission import check as permission_check
    from app.tools import get_registry
    from app.tools.base import ToolContext

    registry = get_registry()
    ctx = ToolContext(
        settings=settings,
        session_id=session_id,
        lifecycle=cfg.get("lifecycle"),
    )

    # ---------- 阶段 1：串行权限检查 ----------

    # 待并行执行的调用
    @dataclass
    class AllowedCall:
        index: int
        tool_name: str
        args: dict
        tool_call_id: str
        tool: Any

    allowed_calls: list[AllowedCall] = []
    # 被拒绝的工具，直接产出 ToolMessage
    rejected_results: list[tuple[int, ToolMessage]] = []
    halted = False

    for idx, tc in enumerate(last_ai.tool_calls):
        tool_name = tc["name"]
        args = tc["args"]
        tool_call_id = tc["id"]

        logger.info(
            "tool_node 检查权限 request_id=%s tool=%s args=%s",
            request_id, tool_name, args,
        )

        # 发开始事件（保持原有语义：一进入 tool_node 就发）
        if on_tool_event:
            await on_tool_event("tool_call_started", {
                "request_id": request_id,
                "tool_call_id": tool_call_id,
                "tool": tool_name,
                "args": args,
            })

        tool = registry.get(tool_name)
        if tool is None:
            msg = f"工具不存在：{tool_name}"
            rejected_results.append((
                idx,
                ToolMessage(content=msg, tool_call_id=tool_call_id),
            ))
            if on_tool_event:
                await on_tool_event("tool_result", {
                    "request_id": request_id,
                    "tool_call_id": tool_call_id,
                    "tool": tool_name,
                    "ok": False,
                    "error": msg,
                })
            continue

        decision = _decide(settings, tool_name, tool.risk_level)

        if decision == Decision.ALLOW:
            allowed_calls.append(AllowedCall(
                index=idx,
                tool_name=tool_name,
                args=args,
                tool_call_id=tool_call_id,
                tool=tool,
            ))
            continue

        if decision == Decision.DENY:
            msg = "权限拒绝"
            rejected_results.append((
                idx,
                ToolMessage(content=msg, tool_call_id=tool_call_id),
            ))
            if on_tool_event:
                await on_tool_event("tool_denied", {
                    "request_id": request_id,
                    "tool_call_id": tool_call_id,
                    "tool": tool_name,
                    "reason": msg,
                })
            continue

        # confirm：发确认请求，等前端回复
        confirm_id = f"{request_id}:{tool_call_id}"
        if on_tool_event:
            await on_tool_event("tool_confirm_request", {
                "request_id": request_id,
                "tool_call_id": tool_call_id,
                "confirm_id": confirm_id,
                "tool": tool_name,
                "args": args,
                "prompt": render_confirm_prompt(
                    persona, tool_name, args
                ),
                "timeout_s": settings.permission.confirm_timeout_s,
            })

        permitted = await permission_check(
            settings,
            tool_name,
            tool.risk_level,
            args,
            request_id=confirm_id,
            pending=pending_registry,
        )

        if not permitted:
            msg = "用户拒绝执行"
            rejected_results.append((
                idx,
                ToolMessage(content=msg, tool_call_id=tool_call_id),
            ))
            if on_tool_event:
                await on_tool_event("tool_denied", {
                    "request_id": request_id,
                    "tool_call_id": tool_call_id,
                    "tool": tool_name,
                    "reason": msg,
                })
            # 她收手那句话
            if on_token is not None:
                await on_token(render_declined(persona))
            halted = True
            break

        # 用户同意
        allowed_calls.append(AllowedCall(
            index=idx,
            tool_name=tool_name,
            args=args,
            tool_call_id=tool_call_id,
            tool=tool,
        ))

    # ---------- 阶段 2：并行执行 ----------

    async def run_one(call: AllowedCall) -> ToolMessage:
        """执行单个工具，异常兜底为错误结果。"""
        try:
            result = await call.tool.run(call.args, ctx)
        except Exception as e:
            logger.exception("工具执行异常：%s", call.tool_name)
            return ToolMessage(
                content=f"工具执行异常：{e}",
                tool_call_id=call.tool_call_id,
            )

        content = (
            result.content if result.ok
            else f"错误：{result.error}"
        )
        # 把结果暂存在 ToolMessage 里，阶段 3 读取
        tm = ToolMessage(
            content=content,
            tool_call_id=call.tool_call_id,
        )
        # 挂上完整结果，供阶段 3 发事件用
        tm._tool_result = result    # type: ignore[attr-defined]
        return tm

    executed: list[ToolMessage] = []
    if allowed_calls:
        logger.info(
            "tool_node 并行执行 %d 个工具 request_id=%s",
            len(allowed_calls), request_id,
        )
        gathered = await asyncio.gather(
            *[run_one(c) for c in allowed_calls],
            return_exceptions=True,
        )
        for item in gathered:
            if isinstance(item, BaseException):
                # 理论上 run_one 已兜底，这里再保一层
                logger.exception("工具并行任务失败：%s", item)
                executed.append(ToolMessage(
                    content=f"工具任务失败：{item}",
                    tool_call_id="",
                ))
            else:
                executed.append(item)

    # ---------- 阶段 3：按原顺序发结果事件 ----------

    # 把 rejected + executed 合并，按 index 排序
    combined: list[tuple[int, ToolMessage]] = list(rejected_results)
    for call, tm in zip(allowed_calls, executed):
        combined.append((call.index, tm))
    combined.sort(key=lambda x: x[0])

    results: list[ToolMessage] = []
    for idx, tm in combined:
        results.append(tm)

        # 从原 tool_calls 拿 tool_name（用于事件）
        if on_tool_event is not None:
            tool_name = last_ai.tool_calls[idx]["name"]
            tool_result = getattr(tm, "_tool_result", None)
            if tool_result is not None:
                await on_tool_event("tool_result", {
                    "request_id": request_id,
                    "tool_call_id": tm.tool_call_id,
                    "tool": tool_name,
                    "ok": tool_result.ok,
                    "content": tool_result.content,
                    "error": tool_result.error,
                    "meta": tool_result.meta,
                })
            # 被拒绝/未找到的已经在阶段 1 发过事件，不重复

    # ---------- 收尾 ----------

    out: dict[str, Any] = {
        "messages": results,
        "tool_steps": tool_steps + 1,
    }
    if halted:
        out["halt"] = True
    return out


def _decide(settings, tool_name: str, risk_level: str):
    """转发到 app.permission.decide，避免模块级循环 import"""

    from app.permission import decide
    return decide(settings, tool_name, risk_level)
