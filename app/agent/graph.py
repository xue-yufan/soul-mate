from __future__ import annotations

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from app.agent.nodes import chat_node, info_node, supervisor_node, tool_node
from app.agent.state import AgentState


def _route_by_next_step(state: AgentState) -> str:
    """supervisor 之后：读 state.next_step，映射到节点名或 END"""

    step = state.get("next_step") or "end"
    if step in ("chat", "info", "end"):
        return step
    # 理论上不会走到这里（intent 只可能是 chat/info）；
    # 真出现未知值时回退 chat，至少让用户拿到一句回复，而不是静默空回
    return "chat"


def _route_after_expert(state: AgentState) -> str:
    """专家节点之后：最后一条 AI 消息带 tool_calls 就去 tool，否则 END"""
    for m in reversed(state.get("messages", [])):
        if isinstance(m, AIMessage):
            if getattr(m, "tool_calls", None):
                return "tool"
            return "end"
    return "end"


def _route_after_tool(state: AgentState) -> str:
    """
    tool 之后：回到发起这次工具调用的专家继续说话；被拒绝/取消则收尾

    没有这个分支的话，拒绝确认会变成"模型原样再调一次同名工具"，
    直到把整轮的 run_timeout_s 耗光。
    """

    if state.get("halt"):
        return "end"

    expert = state.get("current_expert")
    # 目前只会是 chat / info；真出现别的值时回 chat——返回映射表里没有的
    # 分支名，LangGraph 会直接抛错，用户看到的是一条回复都没有
    return expert if expert in ("chat", "info") else "chat"


def build_graph():
    """构建并编译 Agent 图"""

    graph = StateGraph(AgentState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("chat", chat_node)
    graph.add_node("info", info_node)
    graph.add_node("tool", tool_node)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        _route_by_next_step,
        {"chat": "chat", "info": "info", "end": END},
    )
    graph.add_conditional_edges(
        "chat",
        _route_after_expert,
        {"tool": "tool", "end": END},
    )
    graph.add_conditional_edges(
        "info",
        _route_after_expert,
        {"tool": "tool", "end": END},
    )
    graph.add_conditional_edges(
        "tool",
        _route_after_tool,
        {"chat": "chat", "info": "info", "end": END},
    )

    return graph.compile()
