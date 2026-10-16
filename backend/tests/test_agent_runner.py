"""AgentRunner 事件契约：成功路径与错误传播"""

from langchain_core.messages import AIMessage

from app.agent import AgentRunner, build_graph
from app.agent.emotion import EMOTION_INSTRUCTION
from app.config import load_settings
from app.events import now_iso
from app.models.base import ModelAdapter, StreamChunk, ToolCall
from app.storage.db_SQlite import init_db, insert
from app.storage.session import ensure_session
from app.tools.base import BaseTool, ToolResult


class StubAdapter(ModelAdapter):
    """按给定增量产出，或直接抛错"""

    def __init__(self, deltas=(), error: Exception | None = None) -> None:
        self.deltas = list(deltas)
        self.error = error

    async def chat_stream(self, messages, *, tools=None):
        if self.error is not None:
            raise self.error
        for delta in self.deltas:
            yield StreamChunk(delta=delta)
        yield StreamChunk(delta="", finish_reason="stop", tokens_in=5, tokens_out=7)


class RecordingAdapter(ModelAdapter):
    """记录模型实际收到的消息"""

    def __init__(self, deltas=()) -> None:
        self.deltas = list(deltas)
        self.seen: list = []

    async def chat_stream(self, messages, *, tools=None):
        self.seen = list(messages)
        for delta in self.deltas:
            yield StreamChunk(delta=delta)
        yield StreamChunk(delta="", finish_reason="stop", tokens_in=1, tokens_out=1)


class ToolsRecordingAdapter(ModelAdapter):
    """记录每次调用收到的 messages 与 tools 参数"""

    def __init__(self, deltas=()) -> None:
        self.deltas = list(deltas)
        self.calls: list[dict] = []

    async def chat_stream(self, messages, *, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        for delta in self.deltas:
            yield StreamChunk(delta=delta)
        yield StreamChunk(delta="", finish_reason="stop", tokens_in=1, tokens_out=1)


class ErrorStateGraph:
    """固定返回带 error 状态的假图"""

    async def ainvoke(self, state, config=None):
        return {"messages": [], "error": "cancelled"}


def _make_runner(settings, adapter) -> AgentRunner:
    return AgentRunner(
        graph=build_graph(),
        adapter=adapter,
        persona_prompt="你是测试人格。",
        settings=settings,
    )


async def _seed_messages(settings, session_id: str, rows) -> None:
    """按给定 (id, role, content) 落库，模拟 ws 层已经写好的历史"""

    await ensure_session(settings, session_id)
    for mid, role, content in rows:
        await insert(
            settings,
            "INSERT INTO messages(id, session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, session_id, role, content, now_iso()),
        )


async def _collect(runner: AgentRunner) -> list:
    return [event async for event in runner.run("你好")]


async def test_runner_happy_path(tmp_settings):
    runner = _make_runner(tmp_settings, StubAdapter(deltas=["你", "好"]))
    events = await _collect(runner)

    assert events[0].type == "run_started"
    assert [e.data["text"] for e in events if e.type == "text_delta"] == ["你", "好"]

    finished = events[-1]
    assert finished.type == "run_finished"
    assert finished.data["text"] == "你好"
    assert finished.data["tokens_in"] == 5
    assert finished.data["tokens_out"] == 7


async def test_runner_streams_text_when_reply_has_no_emotion_tag(tmp_settings):
    """契约：没有情绪标签时，正文仍逐 delta 透传，不能被缓冲合并"""

    adapter = StubAdapter(deltas=["今天", "天气", "不错"])
    runner = _make_runner(tmp_settings, adapter)
    events = await _collect(runner)

    assert [e.data["text"] for e in events if e.type == "text_delta"] == [
        "今天",
        "天气",
        "不错",
    ]
    assert events[-1].data["text"] == "今天天气不错"


async def test_runner_emits_emotion_then_streams_body(tmp_settings):
    """契约：开头的情绪标签被解析成 emotion 事件，正文照常逐 delta 流出"""

    adapter = StubAdapter(
        deltas=["<emotion>", "happy", "</emotion>\n你", "好"]
    )
    runner = _make_runner(tmp_settings, adapter)
    events = await _collect(runner)

    assert [e.data["emotion"] for e in events if e.type == "emotion"] == ["happy"]
    assert [e.data["text"] for e in events if e.type == "text_delta"] == ["你", "好"]
    assert events[-1].data["text"] == "你好"


async def test_runner_salvages_broken_emotion_close_tag(tmp_settings):
    """模型把闭合标签写坏（</emom>）时：情绪要认出来，标签不能漏进正文"""

    adapter = StubAdapter(
        deltas=["<emotion>", "thinking", "</emom>\n茶姬……", "我未曾尝过。"]
    )
    runner = _make_runner(tmp_settings, adapter)
    events = await _collect(runner)

    assert [e.data["emotion"] for e in events if e.type == "emotion"] == [
        "thinking"
    ]
    assert [e.data["text"] for e in events if e.type == "text_delta"] == [
        "茶姬……",
        "我未曾尝过。",
    ]
    assert events[-1].data["text"] == "茶姬……我未曾尝过。"


async def test_runner_salvages_emotion_tag_left_open(tmp_settings):
    """流结束时标签还没闭合：也要认出情绪，而不是把标签当正文"""

    adapter = StubAdapter(deltas=["<emotion>sad"])
    runner = _make_runner(tmp_settings, adapter)
    events = await _collect(runner)

    assert [e.data["emotion"] for e in events if e.type == "emotion"] == ["sad"]
    assert [e.data["text"] for e in events if e.type == "text_delta"] == []
    assert events[-1].data["text"] == ""


async def test_runner_emits_error_when_adapter_raises(tmp_settings):
    runner = _make_runner(tmp_settings, StubAdapter(error=RuntimeError("boom")))
    events = await _collect(runner)

    # 专家在真正干活前先宣告自己，因此失败路径也会先出现 expert_selected
    assert [e.type for e in events] == [
        "run_started",
        "expert_selected",
        "error",
    ]
    assert events[-1].data["code"] == "model_error"
    assert "boom" in events[-1].data["message"]


async def test_runner_passes_settings_so_history_reaches_model(tmp_env, monkeypatch):
    """回归：settings 必须传进图，否则历史与摘要永远不会加载"""

    monkeypatch.setenv("SOUL_MATE_AGENT__HISTORY_TURNS", "6")
    settings = load_settings()

    await init_db(settings)
    session_id = settings.agent.default_session_id
    await _seed_messages(
        settings,
        session_id,
        (
            ("m1", "user", "我叫小明"),
            ("m2", "assistant", "你好小明"),
            ("m3", "user", "你还记得我叫什么吗"),
        ),
    )

    adapter = RecordingAdapter(deltas=["记得"])
    runner = AgentRunner(
        graph=build_graph(),
        adapter=adapter,
        persona_prompt="你是测试人格。",
        settings=settings,
    )
    events = [
        e
        async for e in runner.run(
            "你还记得我叫什么吗",
            request_id="r1",
            session_id=session_id,
        )
    ]

    assert events[-1].type == "run_finished"
    assert [m.role for m in adapter.seen] == ["system", "user", "assistant", "user"]
    assert [m.content for m in adapter.seen[1:]] == [
        "我叫小明",
        "你好小明",
        "你还记得我叫什么吗",
    ]


async def test_runner_appends_current_input_once_when_excluded(tmp_env, monkeypatch):
    """契约：传了 exclude_message_id 时，历史排除本轮、节点补回恰好一次"""

    monkeypatch.setenv("SOUL_MATE_AGENT__HISTORY_TURNS", "6")
    settings = load_settings()

    await init_db(settings)
    session_id = settings.agent.default_session_id
    await _seed_messages(
        settings,
        session_id,
        (
            ("m1", "user", "我叫小明"),
            ("m2", "assistant", "你好小明"),
            ("m3", "user", "你还记得我叫什么吗"),
        ),
    )

    adapter = RecordingAdapter(deltas=["记得"])
    runner = AgentRunner(
        graph=build_graph(),
        adapter=adapter,
        persona_prompt="你是测试人格。",
        settings=settings,
    )
    events = [
        e
        async for e in runner.run(
            "你还记得我叫什么吗",
            request_id="r2",
            session_id=session_id,
            exclude_message_id="m3",
        )
    ]

    assert events[-1].type == "run_finished"
    seen = [(m.role, m.content) for m in adapter.seen]
    # system 除人格外还带情绪格式指令，只断言前缀与关键内容
    assert seen[0][0] == "system"
    assert seen[0][1].startswith("你是测试人格。")
    assert EMOTION_INSTRUCTION in seen[0][1]
    assert seen[1:] == [
        ("user", "我叫小明"),
        ("assistant", "你好小明"),
        ("user", "你还记得我叫什么吗"),
    ]


async def test_runner_reports_cancelled_instead_of_empty_success(tmp_settings):
    """回归：停机导致本轮无产出时，上报 error 而不是空的 run_finished"""

    runner = _make_runner(tmp_settings, StubAdapter(deltas=["不该出现"]))
    events = [e async for e in runner.run("你好", should_stop=lambda: True)]

    assert [e.type for e in events] == ["run_started", "error"]
    assert events[-1].data["code"] == "cancelled"


async def test_runner_injects_recall_into_system_prompt(tmp_env, monkeypatch):
    """回归：记忆/知识开启时，召回内容要拼进 system prompt"""

    from app.memory.retrieve import RecallItem

    settings = load_settings()
    await init_db(settings)
    settings.features.memory = True
    session_id = settings.agent.default_session_id
    await ensure_session(settings, session_id)

    async def fake_recall_all(settings_, query, **kwargs):
        return [
            RecallItem(
                kind="memory",
                title="",
                content="用户喜欢喝美式",
                score=1.0,
                raw_score=1.0,
                meta={},
            )
        ]

    monkeypatch.setattr("app.memory.retrieve.recall_all", fake_recall_all)

    adapter = RecordingAdapter(deltas=["好"])
    runner = AgentRunner(
        graph=build_graph(),
        adapter=adapter,
        persona_prompt="你是测试人格。",
        settings=settings,
    )
    events = [
        e async for e in runner.run("我喜欢喝什么", session_id=session_id)
    ]

    assert events[-1].type == "run_finished"
    assert adapter.seen[0].role == "system"
    assert "【相关记忆】" in adapter.seen[0].content
    assert "用户喜欢喝美式" in adapter.seen[0].content


async def test_runner_maps_graph_error_state_to_error_event(tmp_settings):
    """回归：图以 state.error 报告失败时，不能当成功回复吞掉"""

    runner = AgentRunner(
        graph=ErrorStateGraph(),
        adapter=StubAdapter(),
        persona_prompt="",
        settings=tmp_settings,
    )
    events = [e async for e in runner.run("你好")]

    assert [e.type for e in events] == ["run_started", "error"]
    assert events[-1].data == {"code": "cancelled", "message": "生成被取消"}


def test_extract_last_ai_text_keeps_plain_string():
    result = {"messages": [AIMessage(content="普通文本")]}

    assert AgentRunner._extract_last_ai_text(result) == "普通文本"


def test_extract_last_ai_text_joins_text_blocks():
    """回归：内容块不能退化成 str(list) 的 repr"""

    result = {
        "messages": [
            AIMessage(
                content=[
                    {"type": "reasoning", "text": "内心戏不该外泄"},
                    {"type": "text", "text": "第一段"},
                    {"type": "text", "text": "第二段"},
                ]
            )
        ]
    }

    assert AgentRunner._extract_last_ai_text(result) == "第一段第二段"


def test_extract_last_ai_text_ignores_tool_call_blocks():
    result = {
        "messages": [
            AIMessage(
                content=[
                    {"type": "tool_call", "id": "1", "name": "search", "args": {}},
                ]
            )
        ]
    }

    assert AgentRunner._extract_last_ai_text(result) == ""


def test_serialize_new_messages_keeps_tool_roundtrip():
    """回归：工具往返要能序列化成可落库的 assistant + tool 消息"""

    from langchain_core.messages import HumanMessage, ToolMessage

    result = {
        "messages": [
            HumanMessage(content="现在几点"),
            AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_1", "name": "get_time", "args": {}}
                ],
            ),
            ToolMessage(content="本地时间：12:00", tool_call_id="call_1"),
            AIMessage(content="现在是 12:00"),
        ]
    }

    assert AgentRunner._serialize_new_messages(result) == [
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
    ]


def test_serialize_new_messages_skips_human_message():
    """本轮用户消息由调用方落库，序列化时必须跳过，否则会落两条"""

    from langchain_core.messages import HumanMessage

    result = {"messages": [HumanMessage(content="你好")]}

    assert AgentRunner._serialize_new_messages(result) == []


async def test_runner_routes_info_intent_to_info_expert(tmp_settings):
    """契约：信息类输入要路由到 info 专家，system prompt 带信息查询职责"""

    adapter = RecordingAdapter(deltas=["不知道"])
    runner = _make_runner(tmp_settings, adapter)

    events = [e async for e in runner.run("今天天气怎么样")]

    assert events[-1].type == "run_finished"
    assert adapter.seen[0].role == "system"
    assert adapter.seen[0].content.startswith("你是测试人格。")
    assert "【当前专家：信息查询】" in adapter.seen[0].content
    # info 已接工具：提示词里承诺了"有工具就优先调用"
    assert "如果系统提供了工具" in adapter.seen[0].content


async def test_runner_chat_intent_has_no_info_hint(tmp_settings):
    """对照：闲聊路由不该带上信息查询的职责提示"""

    adapter = RecordingAdapter(deltas=["嗯"])
    runner = _make_runner(tmp_settings, adapter)

    events = [e async for e in runner.run("你好")]

    assert events[-1].type == "run_finished"
    assert "【当前专家：信息查询】" not in adapter.seen[0].content


async def test_runner_emits_expert_selected_for_chat(tmp_settings):
    """契约：接手专家要对外宣告，前端据此显示「多专家」等待提示"""

    runner = _make_runner(tmp_settings, RecordingAdapter(deltas=["嗯"]))

    events = [e async for e in runner.run("你好")]

    assert [e.data["expert"] for e in events if e.type == "expert_selected"] == [
        "chat"
    ]


async def test_runner_emits_expert_selected_for_info(tmp_settings):
    runner = _make_runner(tmp_settings, RecordingAdapter(deltas=["不知道"]))

    events = [e async for e in runner.run("今天天气怎么样")]

    assert [e.data["expert"] for e in events if e.type == "expert_selected"] == [
        "info"
    ]


async def test_runner_chat_expert_never_receives_tools(tmp_settings):
    """契约：工具只给信息查询专家，闲聊专家不拿（免得她突然开始查东西）"""

    schema = {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "看时间",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    adapter = ToolsRecordingAdapter(deltas=["在的"])
    runner = AgentRunner(
        graph=build_graph(),
        adapter=adapter,
        persona_prompt="你是测试人格。",
        settings=tmp_settings,
        openai_schemas=[schema],
    )

    events = [e async for e in runner.run("你好")]

    assert events[-1].type == "run_finished"
    assert adapter.calls[0]["tools"] is None


async def test_runner_info_expert_receives_tools(tmp_settings):
    """契约：工具给信息查询专家（她的提示词里承诺了会去查）"""

    schema = {
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "看时间",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    adapter = ToolsRecordingAdapter(deltas=["现在是 12:00"])
    runner = AgentRunner(
        graph=build_graph(),
        adapter=adapter,
        persona_prompt="你是测试人格。",
        settings=tmp_settings,
        openai_schemas=[schema],
    )

    events = [e async for e in runner.run("现在几点")]

    assert events[-1].type == "run_finished"
    assert adapter.calls[0]["tools"] == [schema]


class ToolCallingAdapter(ModelAdapter):
    """第一轮请求工具，第二轮给最终回复；记录每次收到的消息与 tools"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat_stream(self, messages, *, tools=None):
        self.calls.append({"messages": list(messages), "tools": tools})
        if len(self.calls) == 1:
            yield StreamChunk(
                tool_calls=[
                    ToolCall(id="call_1", name="get_time", arguments="{}")
                ]
            )
            yield StreamChunk(delta="", finish_reason="tool_calls")
            return
        yield StreamChunk(delta="现在是 12:00")
        yield StreamChunk(delta="", finish_reason="stop")


class FixedTimeTool(BaseTool):
    """固定返回时间，避免用例依赖真实工具注册表"""

    name = "get_time"
    description = "获取当前时间"
    parameters = {"type": "object", "properties": {}, "required": []}
    risk_level = "safe"

    async def run(self, args, ctx) -> ToolResult:
        return ToolResult(ok=True, content="本地时间：12:00")


async def test_runner_info_tool_loop_returns_to_info(
    tmp_settings, monkeypatch
):
    """契约：info 调完工具要回到 info（带着工具结果继续答），不是掉回 chat"""

    monkeypatch.setattr(tmp_settings.permission, "default", "allow")

    import app.tools as tools_pkg

    registry = tools_pkg.ToolRegistry()
    registry.register(FixedTimeTool())
    monkeypatch.setattr(tools_pkg, "get_registry", lambda: registry)

    adapter = ToolCallingAdapter()
    runner = AgentRunner(
        graph=build_graph(),
        adapter=adapter,
        persona_prompt="你是测试人格。",
        settings=tmp_settings,
        openai_schemas=[FixedTimeTool().to_openai_schema()],
    )

    events = [e async for e in runner.run("现在几点")]

    assert events[-1].type == "run_finished"
    assert events[-1].data["text"] == "现在是 12:00"

    # 工具轮次回到 info：专家入口进两次，因此宣告两次（前端可自行去重）
    assert [e.data["expert"] for e in events if e.type == "expert_selected"] == [
        "info",
        "info",
    ]

    # 两次模型调用：第一次由 info 发起工具，第二次仍回 info 并带上工具结果
    assert len(adapter.calls) == 2
    first, second = adapter.calls[0], adapter.calls[1]
    assert "【当前专家：信息查询】" in first["messages"][0].content
    assert first["tools"] is not None
    assert "【当前专家：信息查询】" in second["messages"][0].content
    assert any(
        m.role == "tool" and "本地时间" in m.content
        for m in second["messages"]
    )
