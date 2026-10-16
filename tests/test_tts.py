"""TTS：切句与适配器容错"""

import sys
import types

from app.config import TtsConfig
from app.tts import SentenceSplitter
from app.tts.edge import EdgeTtsAdapter


def test_splitter_emits_punctuation_only_sentence():
    """纯标点会单独成句，上层需要把它挡在合成之前"""

    splitter = SentenceSplitter(TtsConfig())

    assert splitter.feed("……") == ["…", "…"]


async def test_edge_adapter_tolerates_no_audio(monkeypatch):
    """Edge 对纯标点会抛 NoAudioReceived：按空音频处理，不让异常冒泡"""

    class NoAudioReceived(Exception):
        pass

    class FakeCommunicate:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def stream(self):
            raise NoAudioReceived("no audio")
            yield  # pragma: no cover

    fake_edge = types.ModuleType("edge_tts")
    fake_edge.Communicate = FakeCommunicate
    fake_exceptions = types.ModuleType("edge_tts.exceptions")
    fake_exceptions.NoAudioReceived = NoAudioReceived
    fake_edge.exceptions = fake_exceptions
    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge)
    monkeypatch.setitem(sys.modules, "edge_tts.exceptions", fake_exceptions)

    result = await EdgeTtsAdapter(TtsConfig()).synthesize("…")

    assert result.audio == b""
    assert result.chars == 0


async def test_edge_adapter_returns_audio(monkeypatch):
    class FakeCommunicate:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def stream(self):
            yield {"type": "audio", "data": b"mp3-bytes"}

    fake_edge = types.ModuleType("edge_tts")
    fake_edge.Communicate = FakeCommunicate
    fake_exceptions = types.ModuleType("edge_tts.exceptions")
    fake_exceptions.NoAudioReceived = type("NoAudioReceived", (Exception,), {})
    fake_edge.exceptions = fake_exceptions
    monkeypatch.setitem(sys.modules, "edge_tts", fake_edge)
    monkeypatch.setitem(sys.modules, "edge_tts.exceptions", fake_exceptions)

    result = await EdgeTtsAdapter(TtsConfig()).synthesize("你好。")

    assert result.audio == b"mp3-bytes"
    assert result.format == "mp3"
    assert result.chars == 3
