from __future__ import annotations

import re

VALID_EMOTIONS: set[str] = {
    "neutral",
    "happy",
    "sad",
    "surprised",
    "shy",
    "thinking",
    "angry",
}

DEFAULT_EMOTION = "neutral"

# 缓冲区上限：正常标签 25 字符，留 4 倍冗余
MAX_PREFIX = 100

# 匹配开头的 <emotion>xxx</emotion>，可选跟一个换行
_EMOTION_RE = re.compile(
    r"^\s*<emotion>\s*([a-zA-Z]+)\s*</emotion>\s*\n?",
    re.IGNORECASE,
)


def parse_emotion_tag(text: str) -> tuple[str | None, str]:
    """
    尝试从 text 开头解析情绪标签

    返回 (emotion, consumed_length)：
    - 匹配成功：("happy", 27)，表示吃掉前 27 个字符
    - 匹配失败：(None, 0)
    - emotion 非法值会回退到 DEFAULT_EMOTION，但 consumed_length 仍有效

    """
    m = _EMOTION_RE.match(text)
    if not m:
        return None, 0

    raw = m.group(1).lower()
    emotion = raw if raw in VALID_EMOTIONS else DEFAULT_EMOTION
    return emotion, m.end()


# 抢救用：模型偶尔把闭合标签写坏（</emom>、漏闭合、大小写乱来），
# 严格正则认不出来。只认「以 <emotion> 开头、后面跟一个 ASCII 单词」，
# 取值不在白名单里就不动正文，避免把正文里正常的 "<" 吃进去。
_SALVAGE_RE = re.compile(
    r"^\s*<\s*emotion\s*>\s*([a-zA-Z]+)"
    r"\s*(?:</[a-zA-Z]*>?)?"
    r"\s*\n?",
    re.IGNORECASE,
)


def salvage_emotion_prefix(text: str) -> tuple[str | None, int]:
    """
    容错解析开头的情绪标签：不强求闭合标签写对

    返回 (emotion, consumed)；认不出来返回 (None, 0)。
    取值不在 VALID_EMOTIONS 里时按 parse_emotion_tag 的老规矩回退成
    DEFAULT_EMOTION，但标签照样吃掉——标签是协议，不该出现在正文里。
    """

    m = _SALVAGE_RE.match(text)
    if not m:
        return None, 0

    raw = m.group(1).lower()
    emotion = raw if raw in VALID_EMOTIONS else DEFAULT_EMOTION
    return emotion, m.end()


def strip_emotion_prefix(text: str) -> str:
    """
    去掉开头的情绪标签（含写坏的），返回正文；认不出来就原样返回

    给读历史那条路用：老数据里存着模型当初写坏的标签，
    展示之前该清掉。
    """

    emotion, consumed = salvage_emotion_prefix(text)
    if emotion is None:
        return text
    return text[consumed:]


# 开/闭标签字面量，供流式前缀判断逐字符比对
_OPEN_TAG = "<emotion>"
_CLOSE_TAG = "</emotion>"


def is_emotion_prefix(text: str) -> bool:
    """
    判断 text 是否「仍可能只是开头情绪标签的一部分」。

    流式输出时用它决定是缓冲还是直通：
    - True：后面还可能补完 <emotion>xxx</emotion>，值得继续等；
    - False：已经不可能构成开头标签，应立刻当正文发给前端。

    只在 parse_emotion_tag 返回 None（还没匹配到完整标签）之后调用。
    """

    i, n = 0, len(text)

    # 标签前允许空白（与 _EMOTION_RE 的 ^\s* 对齐）
    while i < n and text[i].isspace():
        i += 1
    if i == n:
        return True

    # 开标签逐字符比对，比到一半也算「还可能」
    for expected in _OPEN_TAG:
        if i >= n:
            return True
        if text[i].lower() != expected:
            return False
        i += 1

    # 标签值：\s* [a-zA-Z]+
    while i < n and text[i].isspace():
        i += 1
    letters = 0
    while i < n and text[i].isascii() and text[i].isalpha():
        letters += 1
        i += 1
    if i == n:
        return True
    if letters == 0:
        # 值至少要有字母，走到这里已经不可能是标签了
        return False

    # 值后空白
    while i < n and text[i].isspace():
        i += 1
    if i == n:
        return True

    # 闭标签逐字符比对
    for expected in _CLOSE_TAG:
        if i >= n:
            return True
        if text[i].lower() != expected:
            return False
        i += 1

    # 完整标签本身由 parse_emotion_tag 负责，这里只剩空白才继续等
    return text[i:].strip() == ""


EMOTION_INSTRUCTION = """\
【输出格式】
每次回复必须以情绪标签开头，格式严格如下：
<emotion>happy</emotion>
正文……

可选值（只能选一个）：
- neutral   平静
- happy     开心
- sad       难过
- surprised 惊讶
- shy       害羞
- thinking  思考
- angry     生气

标签必须完整、必须换行、必须放在正文之前。不要输出其他说明。"""
