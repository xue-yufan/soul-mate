"""参考音频按情绪解析的行为（不碰任何引擎）"""

from __future__ import annotations

from pathlib import Path

from app.config import TtsConfig
from app.tts.refs import resolve_reference

DEFAULT = ("D:/default.wav", "默认文本")


def _config(**kwargs) -> TtsConfig:
    base = {"ref_audio": DEFAULT[0], "prompt_text": DEFAULT[1]}
    base.update(kwargs)
    return TtsConfig(**base)


def _write_refs(root: Path, emotion: str, text: str = "情绪文本") -> None:
    (root / f"{emotion}.wav").write_bytes(b"RIFF")
    (root / f"{emotion}.txt").write_text(text, encoding="utf-8")


def test_falls_back_when_no_dir_configured() -> None:
    assert resolve_reference(_config(), "happy") == DEFAULT


def test_falls_back_without_emotion() -> None:
    assert resolve_reference(_config(ref_audio_dir="D:/refs"), None) == DEFAULT


def test_uses_per_emotion_reference(tmp_path: Path) -> None:
    _write_refs(tmp_path, "happy", text="我今天很开心")

    audio, text = resolve_reference(_config(ref_audio_dir=str(tmp_path)), "happy")

    assert audio == str(tmp_path / "happy.wav")
    assert text == "我今天很开心"


def test_falls_back_when_that_emotion_has_no_material(tmp_path: Path) -> None:
    _write_refs(tmp_path, "happy")  # 有 happy，没有 sad

    assert resolve_reference(_config(ref_audio_dir=str(tmp_path)), "sad") == DEFAULT


def test_needs_both_audio_and_text(tmp_path: Path) -> None:
    """半套素材（只有 wav / txt 是空的）要回落——拿来克隆只会得到怪腔调"""

    cfg = _config(ref_audio_dir=str(tmp_path))
    (tmp_path / "angry.wav").write_bytes(b"RIFF")
    assert resolve_reference(cfg, "angry") == DEFAULT

    (tmp_path / "angry.txt").write_text("   \n", encoding="utf-8")
    assert resolve_reference(cfg, "angry") == DEFAULT


def test_unknown_emotion_string_is_ignored(tmp_path: Path) -> None:
    """情绪名会被拼进文件名，白名单外的一律当没有"""

    cfg = _config(ref_audio_dir=str(tmp_path))

    assert resolve_reference(cfg, "../../secret") == DEFAULT
    assert resolve_reference(cfg, "happy.wav") == DEFAULT
