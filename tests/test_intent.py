"""意图分类：规则优先（不调模型）、模型输出解析、各种失败回退 chat"""

from __future__ import annotations

import asyncio

import pytest

from app.agent import nodes as nodes_mod
from app.agent.intent import (
    classify_intent,
    get_intent_stats,
    record_timeout,
    reset_intent_stats,
)
from app.models.base import ModelAdapter, StreamChunk


@pytest.fixture(autouse=True)
def _isolate_stats():
    """统计是模块级全局，逐用例清零，避免互相污染"""

    reset_intent_stats()
    yield
    reset_intent_stats()


class ScriptedAdapter(ModelAdapter):
    """按脚本产出增量（模拟模型输出），并记录被调用次数"""

    def __init__(self, deltas=(), error: Exception | None = None) -> None:
        self.deltas = list(deltas)
        self.error = error
        self.calls = 0

    async def chat_stream(self, messages, *, tools=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        for delta in self.deltas:
            yield StreamChunk(delta=delta)
        yield StreamChunk(delta="", finish_reason="stop")


# 足够长、且不含任何关键词：会真的走模型分类
NEUTRAL = "帮我看看这个方案"


async def test_short_input_goes_chat_without_model_call():
    """短输入直接归 chat：不为了两个字多花一次模型调用"""

    adapter = ScriptedAdapter(deltas=['{"intent": "info"}'])

    assert await classify_intent(adapter, "你好") == "chat"
    assert adapter.calls == 0


async def test_keyword_hit_goes_info_without_model_call():
    adapter = ScriptedAdapter(deltas=['{"intent": "chat"}'])

    assert await classify_intent(adapter, "今天天气怎么样") == "info"
    assert adapter.calls == 0


async def test_short_keyword_input_still_goes_info():
    """「现在几点」只有 4 个字，但关键词优先级要高于长度兜底"""

    adapter = ScriptedAdapter(deltas=['{"intent": "chat"}'])

    assert await classify_intent(adapter, "现在几点") == "info"
    assert adapter.calls == 0


async def test_model_output_json_is_used():
    """增量是分片的，拼接后才能解析"""

    adapter = ScriptedAdapter(deltas=['{"intent"', ': "info"}'])

    assert await classify_intent(adapter, NEUTRAL) == "info"
    assert adapter.calls == 1


async def test_code_fence_is_stripped():
    adapter = ScriptedAdapter(deltas=['```json\n{"intent": "info"}\n```'])

    assert await classify_intent(adapter, NEUTRAL) == "info"


async def test_garbage_output_falls_back_to_chat():
    adapter = ScriptedAdapter(deltas=["我不知道该选哪个"])

    assert await classify_intent(adapter, NEUTRAL) == "chat"


async def test_unknown_intent_falls_back_to_chat():
    adapter = ScriptedAdapter(deltas=['{"intent": "cooking"}'])

    assert await classify_intent(adapter, NEUTRAL) == "chat"


async def test_model_error_falls_back_to_chat():
    """分类只是优化项，调不通也必须能正常聊天"""

    adapter = ScriptedAdapter(error=RuntimeError("boom"))

    assert await classify_intent(adapter, NEUTRAL) == "chat"


# ---------- 聚合统计 ----------


async def test_stats_count_rule_source():
    """规则命中不调模型：total 加一，model_calls 不动"""

    await classify_intent(
        ScriptedAdapter(deltas=['{"intent": "info"}']), "今天天气怎么样"
    )

    stats = get_intent_stats()
    assert stats["total"] == 1
    assert stats["rule"] == 1
    assert stats["model_calls"] == 0
    assert stats["model_success_rate"] == 0.0


async def test_stats_count_model_success():
    await classify_intent(
        ScriptedAdapter(deltas=['{"intent": "info"}']), NEUTRAL
    )

    stats = get_intent_stats()
    assert stats["model"] == 1
    assert stats["model_success_rate"] == 1.0
    assert stats["model_avg_ms"] >= 0.0


async def test_stats_count_unparseable_output_as_fallback():
    """模型答了但认不出意图，算一次失败，而不是「模型成功」"""

    await classify_intent(ScriptedAdapter(deltas=["我不知道该选哪个"]), NEUTRAL)

    stats = get_intent_stats()
    assert stats["fallback"] == 1
    assert stats["model_calls"] == 1
    assert stats["model_success_rate"] == 0.0


async def test_stats_count_model_error_as_fallback():
    await classify_intent(ScriptedAdapter(error=RuntimeError("boom")), NEUTRAL)

    stats = get_intent_stats()
    assert stats["fallback"] == 1
    assert stats["model_success_rate"] == 0.0


def test_stats_record_timeout():
    """超时由调用方回报，也要计入模型失败"""

    record_timeout()

    stats = get_intent_stats()
    assert stats["timeout"] == 1
    assert stats["model_calls"] == 1
    assert stats["model_success_rate"] == 0.0


async def test_stats_success_rate_mixes_success_and_failure():
    await classify_intent(
        ScriptedAdapter(deltas=['{"intent": "info"}']), NEUTRAL
    )
    await classify_intent(ScriptedAdapter(deltas=["garbage"]), NEUTRAL)

    stats = get_intent_stats()
    assert stats["model"] == 1
    assert stats["fallback"] == 1
    assert stats["model_success_rate"] == 0.5


class HangingAdapter(ModelAdapter):
    """分类调用迟迟不返回，用来触发 supervisor 的超时兜底"""

    async def chat_stream(self, messages, *, tools=None):
        await asyncio.sleep(10)
        yield StreamChunk(delta="")


async def test_supervisor_timeout_is_counted(monkeypatch):
    """supervisor 的 wait_for 超时要回报给统计，且照常回退 chat"""

    monkeypatch.setattr(nodes_mod, "_INTENT_TIMEOUT_S", 0.05)

    result = await nodes_mod.supervisor_node(
        {"user_input": NEUTRAL},
        {"configurable": {"adapter": HangingAdapter(), "request_id": "r1"}},
    )

    assert result["next_step"] == "chat"
    stats = get_intent_stats()
    assert stats["timeout"] == 1
    assert stats["model_calls"] == 1
