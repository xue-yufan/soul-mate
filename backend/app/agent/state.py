from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    """图执行的共享状态"""

    # 对话消息，自动追加
    messages: Annotated[list[BaseMessage], add_messages]

    # 本轮用户输入（纯文本，便于 supervisor 判断）
    user_input: str

     # supervisor 决定的路由：chat | info | end
    next_step: str

    # 意图分类结果：chat | info（第一步只写日志，第二步用于路由）
    intent: str

    # 当前活跃专家：工具循环用
    current_expert: str

    # 统计
    tokens_in: int
    tokens_out: int
    model: str

    # 工具调用步数，每次 tool_node 执行 +1
    tool_steps: int

    # 本轮摄像头观察次数；即使抓帧失败也计数，防止模型反复重试
    camera_looks: int

    # 错误信息，供 runner 判断
    error: str | None

    # 工具被拒绝/确认超时后置位：本轮到此收尾，不再回 chat 重试
    halt: bool
