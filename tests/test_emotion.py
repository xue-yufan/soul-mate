"""情绪标签解析与流式前缀判断"""

import pytest

from app.agent.emotion import (
    DEFAULT_EMOTION,
    is_emotion_prefix,
    parse_emotion_tag,
    salvage_emotion_prefix,
    strip_emotion_prefix,
)


def test_parse_emotion_tag_consumes_tag_and_newline():
    text = "<emotion>happy</emotion>\n你好"

    emotion, consumed = parse_emotion_tag(text)

    assert emotion == "happy"
    assert text[consumed:] == "你好"


def test_parse_emotion_tag_unknown_value_falls_back_to_default():
    text = "<emotion>whatever</emotion>"

    emotion, consumed = parse_emotion_tag(text)

    assert emotion == DEFAULT_EMOTION
    assert consumed == len(text)


def test_parse_emotion_tag_without_tag():
    assert parse_emotion_tag("你好") == (None, 0)


@pytest.mark.parametrize(
    "text",
    [
        "",
        " ",
        "<",
        "<emo",
        "<emotion>",
        "<emotion>hap",
        "<emotion>happy</",
        "<emotion>happy</emotion",
        "<emotion>happy</emotion>\n",
    ],
)
def test_is_emotion_prefix_true_while_tag_can_still_complete(text):
    assert is_emotion_prefix(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "你",
        "<3",
        "<emo你",
        "<emotion>你好",
        "<emotion>happy</emo你",
        # 完整标签后面接了正文：由 parse_emotion_tag 负责，不再是「前缀」
        "<emotion>happy</emotion>你好",
    ],
)
def test_is_emotion_prefix_false_for_plain_text(text):
    assert is_emotion_prefix(text) is False


@pytest.mark.parametrize(
    ("text", "emotion", "rest"),
    [
        # 线上那次：闭合标签写坏，少了 "ti"
        ("<emotion>thinking</emom>\n茶姬……", "thinking", "茶姬……"),
        ("<emotion>thinking</emom>", "thinking", ""),
        # 漏了闭合标签
        ("<emotion>sad", "sad", ""),
        ("<emotion>happy</emotion>\n你好", "happy", "你好"),
        # 取值不在白名单：回退成 neutral，但标签照样吃掉
        ("<emotion>whatever</emom>", DEFAULT_EMOTION, ""),
        # 不是标签的东西不许动；认不出来时 rest 就是原文（一个字符都没吃）
        ("", None, ""),
        ("你好", None, "你好"),
        ("<emotion>", None, "<emotion>"),
        ("<emotion>你好", None, "<emotion>你好"),
        ("<emo>happy", None, "<emo>happy"),
    ],
)
def test_salvage_emotion_prefix(text, emotion, rest):
    emo, consumed = salvage_emotion_prefix(text)

    assert emo == emotion
    assert text[consumed:] == rest


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<emotion>thinking</emom>\n茶姬……", "茶姬……"),
        ("<emotion>happy</emotion>\n你好", "你好"),
        ("正常的回复", "正常的回复"),
        ("", ""),
    ],
)
def test_strip_emotion_prefix(text, expected):
    assert strip_emotion_prefix(text) == expected
