"""GET /history/messages：要把工具行与 tool_calls 一起返回"""

from __future__ import annotations

import json

from app.storage.db_SQlite import insert
from app.storage.session import ensure_session


async def _insert_message(
    settings,
    seq: int,
    msg_id: str,
    role: str,
    content: str,
    **cols,
) -> None:
    fields: dict = {
        "id": msg_id,
        "session_id": settings.agent.default_session_id,
        "role": role,
        "content": content,
        # 显式写递增时间，避免同一次测试里几条消息时间相同导致顺序不稳
        "created_at": f"2026-01-01T00:00:{seq:02d}+00:00",
    }
    fields.update(cols)
    names = ", ".join(fields)
    marks = ", ".join("?" * len(fields))
    await insert(
        settings,
        f"INSERT INTO messages({names}) VALUES ({marks})",
        tuple(fields.values()),
    )


async def test_history_returns_tool_rows_and_tool_calls(client, tmp_settings):
    settings = tmp_settings
    session_id = settings.agent.default_session_id
    await ensure_session(settings, session_id)

    await _insert_message(settings, 1, "u1", "user", "现在几点")
    await _insert_message(
        settings,
        2,
        "a1",
        "assistant",
        "",
        tool_calls=json.dumps(
            [{"id": "call_1", "name": "get_time", "args": {}}],
            ensure_ascii=False,
        ),
    )
    await _insert_message(
        settings,
        3,
        "t1",
        "tool",
        "本地时间：12:00",
        tool_call_id="call_1",
        name="get_time",
        status="ok",
    )
    await _insert_message(
        settings, 4, "a2", "assistant", "现在是 12:00",
        tokens_in=3, tokens_out=4, model="fake-model",
    )

    body = client.get("/history/messages").json()

    assert body["total"] == 4
    assert [i["role"] for i in body["items"]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]

    middle, tool_row = body["items"][1], body["items"][2]
    assert middle["tool_calls"] == [
        {"id": "call_1", "name": "get_time", "args": {}}
    ]
    assert tool_row["tool_call_id"] == "call_1"
    assert tool_row["name"] == "get_time"
    assert tool_row["status"] == "ok"
    assert body["items"][3]["tokens_in"] == 3


async def test_history_ignores_system_messages(client, tmp_settings):
    settings = tmp_settings
    await ensure_session(settings, settings.agent.default_session_id)

    await _insert_message(settings, 1, "s1", "system", "不该出现在历史里")
    await _insert_message(settings, 2, "u1", "user", "你好")

    body = client.get("/history/messages").json()

    assert body["total"] == 1
    assert [i["role"] for i in body["items"]] == ["user"]


async def test_history_strips_broken_emotion_tag(client, tmp_settings):
    """模型写坏的情绪标签是协议标记、不是正文，接口不该原样返回"""

    settings = tmp_settings
    await ensure_session(settings, settings.agent.default_session_id)
    await _insert_message(
        settings, 1, "a1", "assistant", "<emotion>thinking</emom>\n茶姬……"
    )
    await _insert_message(settings, 2, "u1", "user", "<emotion>happy</emotion>")

    body = client.get("/history/messages").json()

    # 助手消息里的脏标签清掉
    assert body["items"][0]["content"] == "茶姬……"
    # 用户消息原样返回
    assert body["items"][1]["content"] == "<emotion>happy</emotion>"
