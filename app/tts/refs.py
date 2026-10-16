"""参考音频的解析：按情绪挑一段，挑不到就回落默认

目录约定（`tts.ref_audio_dir` 指向它，情绪名就是协议里那 7 个，
见 app/agent/emotion.py 的 VALID_EMOTIONS）：

    happy.wav + happy.txt        # 音频，以及它逐字对应的文本
    sad.wav   + sad.txt
    ...

挑不到的三种情况一律回落到 `tts.ref_audio` + `tts.prompt_text`：
没配目录 / 这个情绪没素材 / 只有 wav 没有 txt。所以**素材没备齐之前，
行为和以前完全一样**，不会因为开了这个功能就把声音弄坏。
"""

from __future__ import annotations

from pathlib import Path

from app.agent.emotion import VALID_EMOTIONS
from app.config import TtsConfig
from app.logging_config import get_logger

logger = get_logger("tts.refs")


def resolve_reference(tts: TtsConfig, emotion: str | None) -> tuple[str, str]:
    """返回 (参考音频路径, 参考音频文本)"""

    fallback = (tts.ref_audio, tts.prompt_text)

    # emotion 走的是协议白名单，但这里再挡一道：它会被拼进文件名，
    # 不能拿陌生字符串去访问文件系统
    if not tts.ref_audio_dir or not emotion or emotion not in VALID_EMOTIONS:
        return fallback

    base = Path(tts.ref_audio_dir).expanduser()
    audio = base / f"{emotion}.wav"
    text_file = base / f"{emotion}.txt"
    if not audio.is_file() or not text_file.is_file():
        return fallback

    try:
        prompt = text_file.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("读不出参考音频文本，回落默认：%s", text_file)
        return fallback

    if not prompt:
        # 空文本等于没素材：拿半套去克隆只会得到怪腔调
        return fallback

    return str(audio), prompt
