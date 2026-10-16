from __future__ import annotations

from app.config import TtsConfig

# 句末标点（中英文）
_END_PUNCT = set("。！？!?…")
# 从句标点，可断句但优先合并
_CLAUSE_PUNCT = set("，；,;、")
_NEWLINE = "\n"


class SentenceSplitter:
    """
    流式按句切分

    用法：
        sp = SentenceSplitter(config)
        for delta in stream:
            for sentence in sp.feed(delta):
                ...合成...
        for sentence in sp.flush():
            ...合成...
    """

    def __init__(self, config: TtsConfig) -> None:
        self.min_len = config.sentence_min_len
        self.max_len = config.sentence_max_len
        self._buffer = ""

    def feed(self, text: str) -> list[str]:
        """喂入一段文本，返回可以合成的完整句子"""

        self._buffer += text
        sentences: list[str] = []

        while True:
            boundary = self._find_boundary()
            if boundary < 0:
                break

            sentence = self._buffer[: boundary + 1].strip()
            self._buffer = self._buffer[boundary + 1 :]

            if sentence:
                sentences.append(sentence)

        # 缓冲超长，强制切
        if len(self._buffer) >= self.max_len:
            sentence = self._buffer[: self.max_len].strip()
            self._buffer = self._buffer[self.max_len :]
            if sentence:
                sentences.append(sentence)

        return sentences

    def flush(self) -> list[str]:
        """流结束，产出剩余"""

        rest = self._buffer.strip()
        self._buffer = ""
        return [rest] if rest else []

    def _find_boundary(self) -> int:
        """找下一个可切边界，找不到返回 -1"""

        for i, ch in enumerate(self._buffer):
            if ch in _END_PUNCT or ch == _NEWLINE:
                return i
            if ch in _CLAUSE_PUNCT and i >= self.min_len:
                return i
        return -1
