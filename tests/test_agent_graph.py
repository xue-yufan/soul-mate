"""图的路由函数：专家产出、工具回跳、终止条件"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import (
    _route_after_expert,
    _route_after_tool,
    _route_by_next_step,
)


def test_supervisor_route_maps_known_steps():
    assert _route_by_next_step({"next_step": "chat"}) == "chat"
    assert _route_by_next_step({"next_step": "info"}) == "info"
    assert _route_by_next_step({"next_step": "end"}) == "end"


def test_supervisor_route_unknown_step_falls_back_to_chat():
    """未知值不能静默走到没有回复的分支，要回 chat 让用户拿到回复"""

    assert _route_by_next_step({"next_step": "cooking"}) == "chat"


def test_supervisor_route_missing_step_ends_turn():
    """没有 next_step（空状态）沿用原语义：直接收尾，不去猜一个专家"""

    assert _route_by_next_step({}) == "end"
    assert _route_by_next_step({"next_step": ""}) == "end"


def test_route_after_expert_only_goes_to_tool_with_tool_calls():
    with_calls = {
        "messages": [
            HumanMessage(content="现在几点"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call_1", "name": "get_time", "args": {}}],
            ),
        ]
    }
    assert _route_after_expert(with_calls) == "tool"

    plain_reply = {
        "messages": [
            HumanMessage(content="你好"),
            AIMessage(content="在的"),
        ]
    }
    assert _route_after_expert(plain_reply) == "end"
    assert _route_after_expert({}) == "end"


def test_route_after_tool_returns_to_the_expert_that_called_it():
    """info 调完工具要回 info，否则第二次生成会换成 chat 的提示词"""

    assert _route_after_tool({"current_expert": "info"}) == "info"
    assert _route_after_tool({"current_expert": "chat"}) == "chat"


def test_route_after_tool_unknown_expert_falls_back_to_chat():
    """分支名必须在映射表里，否则 LangGraph 直接抛错、这一轮没有回复"""

    assert _route_after_tool({}) == "chat"
    assert _route_after_tool({"current_expert": "cooking"}) == "chat"


def test_route_after_tool_ends_when_halted():
    """工具被拒绝 / 确认超时要收尾，否则模型会原样再调一次同名工具"""

    assert _route_after_tool({"current_expert": "info", "halt": True}) == "end"
    assert _route_after_tool({"current_expert": "chat", "halt": True}) == "end"
