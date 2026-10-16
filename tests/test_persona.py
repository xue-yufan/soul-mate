"""人格加载与 Prompt 构建测试"""

from pathlib import Path

import pytest

from app.persona import PersonaStore, build_system_prompt, load_persona

PERSONA_PATH = Path("app/persona/persona.yaml")


def test_load_persona():
    profile = load_persona(PERSONA_PATH)
    assert profile.name == "遐蝶"
    assert "冥河的女儿" in profile.aliases
    assert "温柔" in profile.personality.core_traits
    assert any("敬语" in s for s in profile.personality.speech_style)
    assert any("羊毛毡" in like for like in profile.likes)
    assert any("肢体接触" in t for t in profile.taboos)


def test_build_prompt_contains_key_facts():
    profile = load_persona(PERSONA_PATH)
    prompt = build_system_prompt(profile)
    assert "遐蝶" in prompt
    assert "使用敬语" in prompt
    assert "羊毛毡" in prompt
    assert "肢体接触" in prompt
    assert "人格优先" in prompt


def test_persona_store_caches():
    store = PersonaStore(PERSONA_PATH)
    store.load()
    p1 = store.prompt
    p2 = store.prompt
    assert p1 == p2
    assert store.name == "遐蝶"


def test_persona_store_unloaded_raises():
    store = PersonaStore(PERSONA_PATH)
    with pytest.raises(RuntimeError):
        _ = store.prompt


def test_load_persona_missing_file():
    with pytest.raises(FileNotFoundError):
        load_persona(Path("not/exist.yaml"))
