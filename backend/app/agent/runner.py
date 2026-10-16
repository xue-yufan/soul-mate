from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from app.config import Settings
from app.logging_config import get_logger
from app.models.base import ModelAdapter

logger = get_logger(__name__)

# 队列终止哨兵：run_graph 结束时一定投递
_SENTINEL_DONE = "__done__"

# 图以 state["error"] 报告失败时，映射为对外错误码与文案
_STATE_ERRORS: dict[str, tuple[str, str]] = {
    "cancelled": ("cancelled", "生成被取消"),
    "tool_loop_limit": ("tool_loop_limit", "工具调用次数超限"),
}


@dataclass
class AgentEvent:
    """Agent 产出的事件"""

    type: str
    data: dict

class AgentRunner:
    """包装 LangGraph 图，产出 AgentEvent"""

    def __init__(
        self,
        graph,
        adapter: ModelAdapter,
        persona_prompt: str,
        settings: Settings,
        openai_schemas: list[dict] | None = None,
        persona=None,
    ) -> None:
        self.graph = graph
        self.adapter = adapter
        self.persona_prompt = persona_prompt
        self.settings = settings
        # 结构化人格：工具确认时用来渲染她的口吻
        self.persona = persona
        self._openai_schemas = openai_schemas or []
        self.recursion_limit = settings.agent.recursion_limit
        self.run_timeout_s = settings.agent.run_timeout_s

    async def run(
        self,
        user_input: str,
        *,
        should_stop: Callable[[], bool] | None = None,
        request_id: str | None = None,
        session_id: str | None = None,
        exclude_message_id: str | None = None,
        on_tool_event: Callable[[str, dict], Awaitable[None]] | None = None,
        pending_registry=None,
    ) -> AsyncIterator[AgentEvent]:
        """执行一轮，逐事件产出"""

        queue: asyncio.Queue = asyncio.Queue()

        async def on_token(text: str) -> None:
            await queue.put(("text_delta", {"text": text}))

        async def on_emotion(emotion: str) -> None:
            await queue.put(("emotion", {"emotion": emotion}))

        async def on_expert_selected(expert: str) -> None:
            await queue.put(("expert_selected", {"expert": expert}))

        async def run_graph() -> None:
            try:
                result = await asyncio.wait_for(
                    self.graph.ainvoke(
                        {
                            "messages": [HumanMessage(content=user_input)],
                            "user_input": user_input,
                            "next_step": "",
                            "tokens_in": 0,
                            "tokens_out": 0,
                            "model": self.settings.model.model,
                            "tool_steps": 0,
                            "error": None,
                        },
                        config={
                            "recursion_limit": self.recursion_limit,
                            "configurable": {
                                "adapter": self.adapter,
                                "persona_prompt": self.persona_prompt,
                                "persona": self.persona,
                                "on_token": on_token,
                                "on_emotion": on_emotion,
                                "on_expert_selected": on_expert_selected,
                                "should_stop": should_stop,
                                "model_name": self.settings.model.model,
                                "request_id": request_id,
                                "session_id": session_id,
                                "settings": self.settings,
                                "exclude_message_id": exclude_message_id,
                                "openai_schemas": self._openai_schemas,
                                "on_tool_event": on_tool_event,
                                "pending_registry": pending_registry,
                            },
                        },
                    ),
                    timeout=self.run_timeout_s,
                )
                await queue.put(("done", result))
            except GraphRecursionError:
                logger.error("图执行超过递归上限：limit=%d", self.recursion_limit)
                await queue.put((
                    "error",
                    {
                        "code": "recursion_limit",
                        "message": "思考步数超限，我可能绕远了",
                    },
                ))

            except asyncio.TimeoutError:
                logger.error("单轮执行超时：timeout=%ds", self.run_timeout_s)
                await queue.put((
                    "error",
                    {
                        "code": "run_timeout",
                        "message": "思考时间过长，请重试",
                    },
                ))

            except asyncio.CancelledError:
                # 取消路径也要投递 error，让消费端知道被取消
                logger.info("图执行被取消")
                try:
                    queue.put_nowait((
                        "error",
                        {"code": "cancelled", "message": "生成被取消"},
                    ))
                except Exception:
                    pass
                raise

            except Exception as e:
                logger.exception("图执行失败")
                await queue.put((
                    "error",
                    {"code": "model_error", "message": str(e)},
                ))

            finally:
                # 无论成功、失败、取消，都投递终止哨兵，保证消费端能退出
                try:
                    queue.put_nowait((_SENTINEL_DONE, {}))
                except Exception:
                    pass

        task = asyncio.create_task(run_graph())

        yield AgentEvent("run_started", {})

        try:
            while True:
                event_type, data = await queue.get()

                # 终止哨兵：run_graph 已结束
                if event_type == _SENTINEL_DONE:
                    break

                if event_type == "text_delta":
                    yield AgentEvent("text_delta", data)
                    continue

                if event_type == "emotion":
                    yield AgentEvent("emotion", data)
                    continue

                if event_type == "expert_selected":
                    yield AgentEvent("expert_selected", data)
                    continue

                if event_type == "done":
                    state_error = data.get("error")
                    if state_error:
                        # 图内部报告失败（如停机取消），不能当成一次成功回复
                        code, message = _STATE_ERRORS.get(
                            str(state_error), ("model_error", str(state_error))
                        )
                        logger.warning("图返回错误状态：error=%s", state_error)
                        yield AgentEvent("error", {"code": code, "message": message})
                        continue

                    text = self._extract_last_ai_text(data)

                    if not text and should_stop is not None and should_stop():
                        # 主管判定无需回复（走到 END）且没有任何产出：
                        # 停机场景下这是"本轮被取消"，而不是一次空回复成功
                        code, message = _STATE_ERRORS["cancelled"]
                        logger.info("停机导致本轮无产出，按取消上报")
                        yield AgentEvent("error", {"code": code, "message": message})
                        continue

                    yield AgentEvent(
                        "run_finished",
                        {
                            "text": text,
                            "tokens_in": data.get("tokens_in") or 0,
                            "tokens_out": data.get("tokens_out") or 0,
                            "model": data.get("model") or "",
                            "messages": self._serialize_new_messages(data),
                        },
                    )
                    continue

                if event_type == "error":
                    yield AgentEvent("error", data)
                    # 忽略错误，等待哨兵
                    continue
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except BaseException:
                pass


    @staticmethod
    def _extract_last_ai_text(result: dict) -> str:
        """从图结果里取最后一条 AI 消息文本"""

        messages = result.get("messages") or []
        for m in reversed(messages):
            if isinstance(m, AIMessage):
                return AgentRunner._content_to_text(m.content)
        return ""

    @staticmethod
    def _serialize_new_messages(result: dict) -> list[dict]:
        """
        把本轮图里新产生的消息序列化成可落库的字典列表。

        过滤掉 HumanMessage（本轮用户消息由调用方落库，避免重复）：
        - AIMessage    → {"role": "assistant", "content": ..., "tool_calls": [...]}
        - ToolMessage  → {"role": "tool", "content": ..., "tool_call_id", "name"}

        调用方（ws 层）据此把工具调用与结果写进 messages 表。
        """

        out: list[dict] = []
        for m in result.get("messages") or []:
            if isinstance(m, AIMessage):
                item: dict = {
                    "role": "assistant",
                    "content": AgentRunner._content_to_text(m.content),
                }
                tool_calls = getattr(m, "tool_calls", None) or []
                if tool_calls:
                    item["tool_calls"] = [
                        {
                            "id": tc.get("id"),
                            "name": tc.get("name"),
                            "args": tc.get("args") or {},
                        }
                        for tc in tool_calls
                    ]
                out.append(item)
            elif isinstance(m, ToolMessage):
                out.append(
                    {
                        "role": "tool",
                        "content": AgentRunner._content_to_text(m.content),
                        "tool_call_id": getattr(m, "tool_call_id", None),
                        "name": getattr(m, "name", None),
                    }
                )
        return out


    @staticmethod
    def _content_to_text(content: object) -> str:
        """
        把 AIMessage.content 归一成纯文本

        content 可能是字符串，也可能是内容块列表
        （如 [{"type": "text", "text": "..."}]） 内容块只取 text 块，
        避免把 reasoning / 工具调用结构当成正文发给前端
        """

        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    text = block.get("text")
                    if block.get("type") == "text" and isinstance(text, str):
                        parts.append(text)
            return "".join(parts)

        if content is None:
            return ""

        return str(content)
