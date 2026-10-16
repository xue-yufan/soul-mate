"""text_input 端到端：事件推送、日志关联与落库"""

import base64
import sqlite3

from app.agent.runner import AgentEvent
from app.tts import SynthesizeResult


class FakeRunner:
    """假 runner：不调模型，直接产出给定事件序列"""

    def __init__(self, events: list[AgentEvent]) -> None:
        self.events = events
        self.calls: list[dict] = []
        # ws 层在回复后会触发摘要，需要 runner 暴露 adapter 属性
        self.adapter = None

    async def run(
        self,
        user_input,
        *,
        should_stop=None,
        request_id=None,
        session_id=None,
        exclude_message_id=None,
        on_tool_event=None,
        pending_registry=None,
    ):
        self.calls.append(
            {
                "user_input": user_input,
                "request_id": request_id,
                "session_id": session_id,
                "exclude_message_id": exclude_message_id,
            }
        )
        for event in self.events:
            # 真实 runner 的工具事件只走 on_tool_event 回调，不混在事件流里
            if on_tool_event is not None and event.type.startswith("tool_"):
                await on_tool_event(event.type, event.data)
                continue
            yield event


def _ok_events() -> list[AgentEvent]:
    return [
        AgentEvent("run_started", {}),
        AgentEvent("text_delta", {"text": "你"}),
        AgentEvent("text_delta", {"text": "好"}),
        AgentEvent(
            "run_finished",
            {
                "text": "你好呀",
                "tokens_in": 3,
                "tokens_out": 4,
                "model": "fake-model",
            },
        ),
    ]


def _rows(db_path, sql: str) -> list[tuple]:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


class FakeTtsAdapter:
    """假 TTS 适配器：不联网，固定返回一段假音频"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(
        self, text: str, *, emotion: str | None = None
    ) -> SynthesizeResult:
        # emotion 收下但不用：这段假音频不区分情绪
        self.calls.append(text)
        return SynthesizeResult(
            audio=b"fake-mp3", format="mp3", text=text, chars=len(text)
        )


def test_text_input_streams_and_persists(client, tmp_settings):
    runner = FakeRunner(_ok_events())
    client.app.state.agent_runner = runner

    # 单会话：session_id 由服务端决定，客户端不传
    session_id = tmp_settings.agent.default_session_id

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "text_input",
                "request_id": "r1",
                "data": {"text": " 在吗 "},
            }
        )
        # 先取并断言首帧：协议出问题时立刻失败，而不是卡在 receive 上
        first = ws.receive_json()
        assert first["type"] == "text_delta"
        got = [first] + [ws.receive_json() for _ in range(2)]

    assert [e["type"] for e in got] == ["text_delta", "text_delta", "run_finished"]
    assert [e["data"]["text"] for e in got[:2]] == ["你", "好"]

    finished = got[-1]
    assert finished["request_id"] == "r1"
    assert finished["session_id"] == session_id
    assert finished["data"]["model"] == "fake-model"
    assert finished["data"]["tokens_in"] == 3
    assert finished["data"]["currency"] == tmp_settings.cost.currency

    # request_id / session_id 必须透传给 runner，否则日志无法关联到请求
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["user_input"] == "在吗"
    assert call["request_id"] == "r1"
    assert call["session_id"] == session_id

    db_path = tmp_settings.paths.sqlite_path
    rows = _rows(db_path, "SELECT id, role, content FROM messages ORDER BY rowid")
    # 必须把本轮 user 的落库 id 交给 runner，否则节点无法在注入历史时排除它
    assert call["exclude_message_id"] == rows[0][0]
    assert [(r[1], r[2]) for r in rows] == [("user", "在吗"), ("assistant", "你好呀")]
    assert _rows(
        db_path, "SELECT provider, model, tokens_in, tokens_out, currency FROM cost_log"
    ) == [(tmp_settings.model.provider, "fake-model", 3, 4, tmp_settings.cost.currency)]


def test_text_input_rejects_foreign_session_id(client, tmp_settings):
    """单会话契约：客户端传别的 session_id 应被拒绝，且不触发模型调用。"""

    runner = FakeRunner(_ok_events())
    client.app.state.agent_runner = runner

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "text_input",
                "request_id": "r9",
                "session_id": "someone-else",
                "data": {"text": "在吗"},
            }
        )
        resp = ws.receive_json()

    assert resp["type"] == "error"
    assert resp["data"]["code"] == "bad_request"
    assert tmp_settings.agent.default_session_id in resp["data"]["message"]
    assert runner.calls == []


def test_text_input_reports_runner_error(client, tmp_settings):
    runner = FakeRunner(
        [
            AgentEvent("run_started", {}),
            AgentEvent("error", {"code": "model_error", "message": "模型调用失败"}),
        ]
    )
    client.app.state.agent_runner = runner

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "text_input",
                "request_id": "r2",
                "data": {"text": "在吗"},
            }
        )
        resp = ws.receive_json()

    assert resp["type"] == "error"
    assert resp["data"]["code"] == "model_error"
    assert resp["request_id"] == "r2"

    # 失败轮次不应写入 assistant 消息
    db_path = tmp_settings.paths.sqlite_path
    assert _rows(db_path, "SELECT role FROM messages") == [("user",)]


def test_text_input_streams_tts_audio(client, tmp_settings):
    """features.tts 打开时：整句合成后推送音频，收尾后把字符数补进 cost_log"""

    runner = FakeRunner(_ok_events())
    client.app.state.agent_runner = runner
    client.app.state.settings.features.tts = True
    tts = FakeTtsAdapter()
    client.app.state.tts_adapter = tts

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "text_input",
                "request_id": "r3",
                "data": {"text": "在吗"},
            }
        )
        got = [ws.receive_json() for _ in range(5)]

    # 切句在收尾时才凑成整句，所以 TTS 事件一定排在 run_finished 之后
    assert [e["type"] for e in got] == [
        "text_delta",
        "text_delta",
        "run_finished",
        "tts_audio_chunk",
        "tts_finished",
    ]

    chunk = got[3]
    assert chunk["request_id"] == "r3"
    assert chunk["data"]["text"] == "你好"
    assert chunk["data"]["format"] == "mp3"
    assert chunk["data"]["index"] == 0
    assert base64.b64decode(chunk["data"]["audio"]) == b"fake-mp3"

    assert got[4]["data"] == {"chunks": 1, "chars": 2}
    assert tts.calls == ["你好"]

    db_path = tmp_settings.paths.sqlite_path
    # tts_finished 在记账之后才发出，所以收到它时 tts_chars 一定已落库
    assert _rows(db_path, "SELECT tts_chars FROM cost_log") == [(2,)]


def test_text_input_tts_sums_sentences(client, tmp_settings):
    """多句：逐句推送、index 递增，字符数累加进 cost_log"""

    from app.agent.runner import AgentEvent

    runner = FakeRunner(
        [
            AgentEvent("text_delta", {"text": "你好。"}),
            AgentEvent("text_delta", {"text": "再见。"}),
            AgentEvent(
                "run_finished",
                {
                    "text": "你好。再见。",
                    "tokens_in": 1,
                    "tokens_out": 1,
                    "model": "fake-model",
                },
            ),
        ]
    )
    client.app.state.agent_runner = runner
    client.app.state.settings.features.tts = True
    tts = FakeTtsAdapter()
    client.app.state.tts_adapter = tts

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "text_input",
                "request_id": "r4",
                "data": {"text": "在吗"},
            }
        )
        got = [ws.receive_json() for _ in range(6)]

    assert got[-1]["type"] == "tts_finished"
    chunks = [e for e in got if e["type"] == "tts_audio_chunk"]
    assert [c["data"]["text"] for c in chunks] == ["你好。", "再见。"]
    assert [c["data"]["index"] for c in chunks] == [0, 1]
    assert got[-1]["data"] == {"chunks": 2, "chars": 6}
    assert tts.calls == ["你好。", "再见。"]

    db_path = tmp_settings.paths.sqlite_path
    assert _rows(db_path, "SELECT tts_chars FROM cost_log") == [(6,)]


def test_text_input_tts_skips_punctuation_only(client, tmp_settings):
    """"……" 这类纯标点没有可发音内容，不该送去合成"""

    from app.agent.runner import AgentEvent

    runner = FakeRunner(
        [
            AgentEvent("text_delta", {"text": "……"}),
            AgentEvent("text_delta", {"text": "在的。"}),
            AgentEvent(
                "run_finished",
                {
                    "text": "……在的。",
                    "tokens_in": 1,
                    "tokens_out": 1,
                    "model": "fake-model",
                },
            ),
        ]
    )
    client.app.state.agent_runner = runner
    client.app.state.settings.features.tts = True
    tts = FakeTtsAdapter()
    client.app.state.tts_adapter = tts

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "text_input",
                "request_id": "r5",
                "data": {"text": "在吗"},
            }
        )
        got = [ws.receive_json() for _ in range(5)]

    kinds = [e["type"] for e in got]
    # "在的。" 是完整句子，会在生成过程中就送去合成，所以只断言次数与收尾
    assert kinds.count("tts_audio_chunk") == 1
    assert "run_finished" in kinds
    assert kinds[-1] == "tts_finished"
    assert tts.calls == ["在的。"]
    assert got[-1]["data"] == {"chunks": 1, "chars": 3}

    db_path = tmp_settings.paths.sqlite_path
    assert _rows(db_path, "SELECT tts_chars FROM cost_log") == [(3,)]


def test_text_input_notifies_once_when_memory_unavailable(client, monkeypatch):
    """嵌入模型不可用：先提示一次降级原因，再照常完成这一轮对话"""

    client.app.state.settings.features.memory = True
    client.app.state.agent_runner = FakeRunner(_ok_events())

    class UnavailableEmbedder:
        unavailable_reason = "模型文件缺失"

    monkeypatch.setattr(
        "app.api.ws.get_embedder", lambda settings: UnavailableEmbedder()
    )

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "text_input",
                "request_id": "r1",
                "data": {"text": "你好"},
            }
        )

        # 第一条先收到降级提示
        warn = ws.receive_json()
        assert warn["type"] == "error"
        assert warn["data"]["code"] == "memory_unavailable"
        assert "模型文件缺失" in warn["data"]["message"]
        assert warn["request_id"] == "r1"

        # 提示之后事件流照常，不阻塞回复
        # 注意：ws 层不转发 run_started，正文从第一条 text_delta 开始
        kinds = []
        while True:
            evt = ws.receive_json()
            kinds.append(evt["type"])
            if evt["type"] == "run_finished":
                break
        assert kinds.count("text_delta") == 2
        assert kinds[-1] == "run_finished"

        # 同一条连接的第二轮不再重复提示
        ws.send_json(
            {
                "v": 1,
                "type": "text_input",
                "request_id": "r2",
                "data": {"text": "在吗"},
            }
        )
        assert ws.receive_json()["type"] == "text_delta"


def test_text_input_persists_tool_roundtrip(client, tmp_settings):
    """工具调用与结果要落进 messages 表：assistant(带 tool_calls) + tool + assistant"""

    runner = FakeRunner(
        [
            AgentEvent("run_started", {}),
            AgentEvent(
                "tool_call_started",
                {
                    "request_id": "r6",
                    "tool_call_id": "call_1",
                    "tool": "get_time",
                    "args": {},
                },
            ),
            AgentEvent(
                "tool_result",
                {
                    "request_id": "r6",
                    "tool_call_id": "call_1",
                    "tool": "get_time",
                    "ok": True,
                    "content": "本地时间：12:00",
                },
            ),
            AgentEvent("text_delta", {"text": "现在是 12:00"}),
            AgentEvent(
                "run_finished",
                {
                    "text": "现在是 12:00",
                    "tokens_in": 3,
                    "tokens_out": 4,
                    "model": "fake-model",
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {"id": "call_1", "name": "get_time", "args": {}}
                            ],
                        },
                        {
                            "role": "tool",
                            "content": "本地时间：12:00",
                            "tool_call_id": "call_1",
                            "name": None,
                        },
                        {"role": "assistant", "content": "现在是 12:00"},
                    ],
                },
            ),
        ]
    )
    client.app.state.agent_runner = runner

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "text_input",
                "request_id": "r6",
                "data": {"text": "现在几点"},
            }
        )
        while True:
            if ws.receive_json()["type"] == "run_finished":
                break

    db_path = tmp_settings.paths.sqlite_path
    rows = _rows(
        db_path,
        "SELECT id, role, content, tool_calls, tool_call_id, status, "
        "tokens_in, tokens_out FROM messages ORDER BY created_at, rowid",
    )

    assert [r[1] for r in rows] == ["user", "assistant", "tool", "assistant"]
    # 中间那条 assistant 带 tool_calls，但不记 token；token 归最终回复
    assert "call_1" in rows[1][3]
    assert rows[1][6] == 0 and rows[1][7] == 0
    assert rows[3][6] == 3 and rows[3][7] == 4
    # tool 行带 tool_call_id 与结果状态
    assert rows[2][4] == "call_1"
    assert rows[2][5] == "ok"

    # 成本绑到最后一条 assistant 消息
    assert _rows(db_path, "SELECT message_id, tokens_in FROM cost_log") == [
        (rows[3][0], 3)
    ]

    # 工具调用要进审计表：谁、调了什么、参数、结果
    assert _rows(
        db_path,
        "SELECT event_type, actor, action, target_id, status FROM audit_log",
    ) == [("tool_call", "assistant", "get_time", "call_1", "ok")]


def test_text_input_forwards_expert_selected(client, tmp_settings):
    """expert_selected 要原样透传给前端，前端才能显示专家等待提示"""

    runner = FakeRunner(
        [
            AgentEvent("run_started", {}),
            AgentEvent("expert_selected", {"expert": "info"}),
            AgentEvent("text_delta", {"text": "在查"}),
            AgentEvent(
                "run_finished",
                {
                    "text": "在查",
                    "tokens_in": 1,
                    "tokens_out": 1,
                    "model": "fake-model",
                },
            ),
        ]
    )
    client.app.state.agent_runner = runner

    with client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "v": 1,
                "type": "text_input",
                "request_id": "r7",
                "data": {"text": "现在几点"},
            }
        )
        frames = []
        while True:
            frame = ws.receive_json()
            frames.append(frame)
            if frame["type"] == "run_finished":
                break

    selected = [f for f in frames if f["type"] == "expert_selected"]
    assert len(selected) == 1
    assert selected[0]["data"]["expert"] == "info"
    assert selected[0]["request_id"] == "r7"
