"""history：历史加载的排除语义与摘要触发边界"""

from app.config import load_settings
from app.events import now_iso
from app.models.base import ModelAdapter, StreamChunk
from app.storage.db_SQlite import init_db, insert
from app.storage.history import load_recent_messages, load_summary, maybe_summarize
from app.storage.session import ensure_session


class StubSummaryAdapter(ModelAdapter):
    """摘要调用固定返回一段文本"""

    def __init__(self, text: str = "摘要文本") -> None:
        self.text = text
        self.calls = 0

    async def chat_stream(self, messages, *, tools=None):
        self.calls += 1
        yield StreamChunk(delta=self.text)


async def _seed(settings, session_id: str, rows) -> None:
    """按给定 (id, role, content) 落库"""

    await ensure_session(settings, session_id)
    for mid, role, content in rows:
        await insert(
            settings,
            "INSERT INTO messages(id, session_id, role, content, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (mid, session_id, role, content, now_iso()),
        )


async def test_load_recent_messages_excludes_given_id(tmp_env):
    """回归：exclude_message_id 必须真的把那条消息排除掉"""

    settings = load_settings()
    await init_db(settings)
    session_id = settings.agent.default_session_id
    await _seed(
        settings,
        session_id,
        (
            ("m1", "user", "第一句"),
            ("m2", "assistant", "第一答"),
            ("m3", "user", "第二句"),
        ),
    )

    kept = await load_recent_messages(
        settings, session_id, max_turns=6, exclude_message_id="m3"
    )

    assert [(m.role, m.content) for m in kept] == [
        ("user", "第一句"),
        ("assistant", "第一答"),
    ]


async def test_load_recent_messages_without_exclude_keeps_all(tmp_env):
    settings = load_settings()
    await init_db(settings)
    session_id = settings.agent.default_session_id
    await _seed(
        settings,
        session_id,
        (("m1", "user", "第一句"), ("m2", "assistant", "第一答")),
    )

    kept = await load_recent_messages(settings, session_id, max_turns=6)

    assert [(m.role, m.content) for m in kept] == [
        ("user", "第一句"),
        ("assistant", "第一答"),
    ]


async def test_maybe_summarize_summarizes_everything_when_keep_turns_zero(
    tmp_env, monkeypatch
):
    """回归：keep_recent=0 表示全部摘要，而不是空切片导致静默不做事"""

    monkeypatch.setenv("SOUL_MATE_AGENT__HISTORY_TURNS", "6")
    settings = load_settings()
    settings.agent.summary_trigger_tokens = 1
    settings.agent.summary_keep_recent_turns = 0

    await init_db(settings)
    session_id = settings.agent.default_session_id
    await _seed(
        settings,
        session_id,
        (("m1", "user", "第一句"), ("m2", "assistant", "第一答")),
    )

    adapter = StubSummaryAdapter("摘要文本")
    await maybe_summarize(settings, session_id, adapter)

    assert adapter.calls == 1
    assert await load_summary(settings, session_id) == "摘要文本"


async def test_maybe_summarize_skips_when_all_messages_are_recent(tmp_env, monkeypatch):
    """消息全都落在保留窗口内时不调用模型"""

    monkeypatch.setenv("SOUL_MATE_AGENT__HISTORY_TURNS", "6")
    settings = load_settings()
    settings.agent.summary_trigger_tokens = 1
    settings.agent.summary_keep_recent_turns = 2

    await init_db(settings)
    session_id = settings.agent.default_session_id
    await _seed(
        settings,
        session_id,
        (("m1", "user", "第一句"), ("m2", "assistant", "第一答")),
    )

    adapter = StubSummaryAdapter()
    await maybe_summarize(settings, session_id, adapter)

    assert adapter.calls == 0
    assert await load_summary(settings, session_id) is None
