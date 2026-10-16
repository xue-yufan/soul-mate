from __future__ import annotations

import json
import re
import time

from app.logging_config import get_logger
from app.models.base import ChatMessage, ModelAdapter

logger = get_logger("agent.intent")

VALID_INTENTS: set[str] = {"chat", "info"}
DEFAULT_INTENT = "chat"

# 短于此长度、且没有关键词命中时直接归 chat，不调模型
MIN_LEN_FOR_MODEL = 5

# 关键词 → 意图（命中即路由）
_KEYWORD_MAP: list[tuple[tuple[str, ...], str]] = [
    (
        (
            "天气", "气温", "下雨", "多云", "晴天", "雨伞",
        ),
        "info",
    ),
    (
        (
            "几点", "时间", "日期", "今天几号", "星期几",
            "现在是", "现在是几",
        ),
        "info",
    ),
    (
        ("新闻", "汇率", "股价", "实时"),
        "info",
    ),
]

_INTENT_PROMPT = """\
你是一个意图分类器。根据用户输入，判断应交给哪个专家处理。

可选专家：
- chat  闲聊、情感交流、陪伴、日常对话
- info  查询实时信息：时间、天气、新闻、汇率等

只输出 JSON，格式严格如下，不要输出其他内容：
{{"intent": "chat"}}

不确定时选 chat。问候、情绪表达、哲学问题一律归为 chat。

用户输入：{user_input}
"""


def _rule_based(text: str) -> str | None:
    """
    关键词快速路由：命中返回意图，否则 None

    关键词必须排在长度兜底之前：用户问时间/天气就是「现在几点」「今天天气」
    这种短句，先按长度判 chat 会让它们永远进不了关键词表。
    """

    for keywords, intent in _KEYWORD_MAP:
        if any(kw in text for kw in keywords):
            return intent

    if len(text) < MIN_LEN_FOR_MODEL:
        return DEFAULT_INTENT

    return None


def _try_parse_intent(raw: str) -> str | None:
    """
    从模型输出里提取意图，提取不到返回 None

    与 _parse_intent 的区别：None 表示「模型给了，但认不出来」，
    统计里算一次失败；能解析出一句合法的 chat 不算失败。
    """

    text = raw.strip()

    # 去 markdown 代码围栏
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    # 直接 JSON
    try:
        data = json.loads(text)
        intent = data.get("intent")
        if isinstance(intent, str) and intent in VALID_INTENTS:
            return intent
    except json.JSONDecodeError:
        pass

    # 正则兜底
    m = re.search(r'"intent"\s*:\s*"(\w+)"', text)
    if m and m.group(1) in VALID_INTENTS:
        return m.group(1)

    return None


def _parse_intent(raw: str) -> str:
    """从模型输出里提取意图，失败返回 chat"""

    return _try_parse_intent(raw) or DEFAULT_INTENT


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


# ---------- 聚合统计 ----------

# 每累计这么多次分类打一条聚合日志。单用户场景日志量小，按次数比按时间窗好测
_STATS_LOG_EVERY = 20


class _IntentStats:
    """
    进程内累计计数：分类来源分布、模型成功率、平均耗时。

    source 取值：rule（规则/长度兜底）/ model（模型分类成功）/
    fallback（模型报错或输出不可解析）/ timeout（分类超时，由调用方回报）。
    """

    def __init__(self) -> None:
        self.total = 0
        self.rule = 0
        self.model = 0
        self.fallback = 0
        self.timeout = 0
        self.rule_ms_total = 0.0
        self.model_ms_total = 0.0

    def snapshot(self) -> dict[str, float | int]:
        model_calls = self.model + self.fallback + self.timeout
        return {
            "total": self.total,
            "rule": self.rule,
            "model": self.model,
            "fallback": self.fallback,
            "timeout": self.timeout,
            "model_calls": model_calls,
            "model_success_rate": (
                self.model / model_calls if model_calls else 0.0
            ),
            "rule_avg_ms": (
                self.rule_ms_total / self.rule if self.rule else 0.0
            ),
            "model_avg_ms": (
                self.model_ms_total / self.model if self.model else 0.0
            ),
        }


_stats = _IntentStats()


def get_intent_stats() -> dict[str, float | int]:
    """返回意图分类统计快照（进程内累计，供测试与运维读取）"""

    return _stats.snapshot()


def reset_intent_stats() -> None:
    """清零统计（测试隔离用）"""

    global _stats
    _stats = _IntentStats()


def record_timeout() -> None:
    """
    记一次「分类超时兜底」。

    超时由调用方用 asyncio.wait_for 控制（见 nodes.supervisor_node），
    本模块感知不到，所以由调用方主动回报。
    """

    _record_source("timeout", 0.0)


def _record_source(source: str, elapsed_ms: float) -> None:
    """累计一次分类结果，并按固定间隔打聚合日志"""

    _stats.total += 1
    if source == "rule":
        _stats.rule += 1
        _stats.rule_ms_total += elapsed_ms
    elif source == "model":
        _stats.model += 1
        _stats.model_ms_total += elapsed_ms
    elif source == "fallback":
        _stats.fallback += 1
    elif source == "timeout":
        _stats.timeout += 1
    else:
        return

    if _stats.total % _STATS_LOG_EVERY == 0:
        s = _stats.snapshot()
        logger.info(
            "意图分类统计：total=%d rule=%d model=%d fallback=%d timeout=%d "
            "model_success=%.1f%% rule_avg_ms=%.1f model_avg_ms=%.1f",
            s["total"], s["rule"], s["model"], s["fallback"], s["timeout"],
            s["model_success_rate"] * 100,
            s["rule_avg_ms"], s["model_avg_ms"],
        )


async def classify_intent(
    adapter: ModelAdapter,
    user_input: str,
    *,
    request_id: str | None = None,
) -> str:
    """
    判断意图，返回 chat / info

    优先级：
    1. 规则命中（短输入 / 关键词）
    2. 模型分类
    3. 失败回退 chat
    """

    text = user_input.strip()
    if not text:
        return DEFAULT_INTENT

    started = time.perf_counter()

    rule_result = _rule_based(text)
    if rule_result is not None:
        _record_source("rule", _elapsed_ms(started))
        logger.debug(
            "意图规则命中：intent=%s source=rule elapsed_ms=%.1f request_id=%s",
            rule_result, _elapsed_ms(started), request_id,
        )
        return rule_result

    prompt = _INTENT_PROMPT.format(user_input=text)

    chunks: list[str] = []
    try:
        async for chunk in adapter.chat_stream([
            ChatMessage(role="user", content=prompt)
        ]):
            if chunk.delta:
                chunks.append(chunk.delta)
    except Exception:
        _record_source("fallback", _elapsed_ms(started))
        logger.exception(
            "意图分类调用失败，回退 chat source=fallback elapsed_ms=%.1f "
            "request_id=%s",
            _elapsed_ms(started), request_id,
        )
        return DEFAULT_INTENT

    raw = "".join(chunks)
    parsed = _try_parse_intent(raw)
    if parsed is None:
        _record_source("fallback", _elapsed_ms(started))
        logger.debug(
            "意图模型分类：输出不可解析，回退 chat source=fallback "
            "elapsed_ms=%.1f raw=%r request_id=%s",
            _elapsed_ms(started), raw[:100], request_id,
        )
        return DEFAULT_INTENT

    _record_source("model", _elapsed_ms(started))
    logger.debug(
        "意图模型分类：intent=%s source=model elapsed_ms=%.1f raw=%r "
        "request_id=%s",
        parsed, _elapsed_ms(started), raw[:100], request_id,
    )
    return parsed
