"""记忆抽取：prompt 渲染与模型输出解析"""

from app.memory.extract import _EXTRACT_PROMPT, _parse_facts, extract_facts
from app.models.base import StreamChunk


class FakeAdapter:
    """只回一段固定文本的假适配器"""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def chat_stream(self, messages, *, tools=None):
        self.prompts.append(messages[-1].content)
        yield StreamChunk(delta=self.reply)


def test_extract_prompt_renders_json_example():
    """回归：模板里的 JSON 示例必须转义，否则 str.format 会 KeyError('\"type\"')"""

    prompt = _EXTRACT_PROMPT.format(user_text="我叫小明", assistant_text="你好小明")

    assert "我叫小明" in prompt
    assert "你好小明" in prompt
    assert '{"type": "preference", "content": "用户喜欢喝美式", "importance": 0.7}' in prompt


async def test_extract_facts_parses_model_json():
    adapter = FakeAdapter(
        '[{"type": "fact", "content": "用户的名字是小明", "importance": 0.9}]'
    )

    facts = await extract_facts(adapter, "我叫小明", "你好小明")

    assert facts == [
        {"type": "fact", "content": "用户的名字是小明", "importance": 0.9}
    ]
    # prompt 必须带上本轮原文，否则模型无从抽取
    assert "我叫小明" in adapter.prompts[0]


def test_parse_facts_skips_invalid_items():
    facts = _parse_facts('[{"content": "用户喜欢美式"}, {"type": "fact"}, "x"]')

    assert facts == [
        {"type": "fact", "content": "用户喜欢美式", "importance": 0.5}
    ]
