"""连后端 WebSocket，收到 TTS 音频就自动播放。

Windows 用 MCI（winmm.dll），纯 ctypes，无第三方依赖。
"""

import asyncio
import base64
import ctypes
import json
import sys
import tempfile
from pathlib import Path

import websockets

# ---------- MCI 播放 ----------

_winmm = ctypes.WinDLL("winmm")
_mci = _winmm.mciSendStringW


def _play_mp3(file_path: str) -> None:
    """用 Windows MCI 播放 mp3，阻塞到播放完成。"""
    alias = "tts_mp3"

    # 打开
    ret = _mci(
        f'open "{file_path}" type mpegvideo alias {alias}',
        None, 0, None,
    )
    if ret != 0:
        raise RuntimeError(f"MCI open 失败：code={ret}")

    try:
        # 播放并等待完成
        ret = _mci(f'play {alias} wait', None, 0, None)
        if ret != 0:
            raise RuntimeError(f"MCI play 失败：code={ret}")
    finally:
        _mci(f"close {alias}", None, 0, None)


async def _play_async(file_path: str) -> None:
    """在线程池里跑阻塞的 MCI 播放，不阻塞事件循环。"""
    await asyncio.to_thread(_play_mp3, file_path)


async def _play_audio_bytes(audio: bytes) -> None:
    """把音频字节写临时文件，播放，删除。"""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(audio)
        path = f.name
    try:
        await _play_async(path)
    finally:
        Path(path).unlink(missing_ok=True)


# ---------- 主逻辑 ----------

async def main(url: str = "ws://127.0.0.1:8000/ws", text: str = "你好") -> None:
    async with websockets.connect(url) as ws:
        print(f"已连接 {url}")
        print(f"发送：{text}\n")

        await ws.send(json.dumps({
            "v": 1,
            "type": "text_input",
            "request_id": "r1",
            "data": {"text": text},
        }))

        async for raw in ws:
            msg = json.loads(raw)
            t = msg.get("type")

            if t == "text_delta":
                print(msg["data"]["text"], end="", flush=True)

            elif t == "tts_audio_chunk":
                audio = base64.b64decode(msg["data"]["audio"])
                idx = msg["data"].get("index", "?")
                print(f"\n[播放第 {idx} 段，{len(audio)} 字节]")
                await _play_audio_bytes(audio)

            elif t == "tts_finished":
                data = msg["data"]
                print(f"\n[TTS 完成：{data['chunks']} 段，{data['chars']} 字符]")

            elif t == "run_finished":
                print(f"\n[轮次结束]")

            elif t == "error":
                print(f"\n[错误] {msg['data']}")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8000/ws"
    text = sys.argv[2] if len(sys.argv) > 2 else "你好"
    asyncio.run(main(url, text))