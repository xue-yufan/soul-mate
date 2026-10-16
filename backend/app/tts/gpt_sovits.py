"""GPT-SoVITS HTTP 适配器

对接 `api_v2.py` 起的本地服务（默认 http://127.0.0.1:9880）：

    POST /tts
    {
      "text":            "要合成的文本",
      "text_lang":       "zh",
      "ref_audio_path":  "D:/GPT-SoVITS/ref.wav",
      "prompt_text":     "参考音频对应的文本",
      "prompt_lang":     "zh",
      "media_type":      "wav",
      "streaming_mode":  false
    }
    → 200, Content-Type: audio/wav, body 是 wav 字节

配置（TtsConfig）：
- base_url：引擎根地址，例如 http://127.0.0.1:9880
- ref_audio：参考音频的绝对路径（Windows 上用正斜杠 /）
- prompt_text：参考音频逐字对应的文本
- timeout_s：单句合成超时；CPU 首次调用会加载模型，留够
- ref_audio_dir：按情绪分文件的参考音频目录（<情绪>.wav + <情绪>.txt）；
  留空或缺素材时回落到 ref_audio + prompt_text
- temperature / top_k / top_p：采样参数，控制"起伏"；默认与引擎默认一致

三件在子类才需要知道的事：
1. api_v2 直接返回带容器头的 wav，不用套头。
2. 语言先固定 "zh"；要支持多语言时，从 TtsConfig 加字段，别在这里写死第二处。
3. base_url / ref_audio / prompt_text 任一为空 → 不出声，记 warning。
   这是配置没填对，不是运行时故障；对话继续，只是没声音。
"""

from __future__ import annotations

from pathlib import Path

import httpx

from app.config import TtsConfig
from app.logging_config import get_logger
from app.tts.http_base import HttpTtsAdapter
from app.tts.refs import resolve_reference

logger = get_logger("tts.gpt_sovits")


class GptSovitsAdapter(HttpTtsAdapter):
    """调本地 GPT-SoVITS api_v2 的 HTTP 适配器"""

    default_format = "wav"

    def __init__(
        self,
        config: TtsConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config, client=client)
        # 情绪参考音频只在配了目录且目录存在时才生效。提醒一次就够——
        # 合成是逐句调用的，放进 _request_audio 会刷屏
        if config.ref_audio_dir and not Path(
            config.ref_audio_dir
        ).expanduser().is_dir():
            logger.warning(
                "ref_audio_dir 不存在，情绪参考音频不会生效：%s", config.ref_audio_dir
            )

    async def _request_audio(
        self, text: str, *, emotion: str | None = None
    ) -> tuple[bytes, str]:
        # 配置完整性：缺就静默（返回空音频），不抛——基类会转成空结果
        if not self.config.base_url:
            logger.warning("GPT-SoVITS 未配置 base_url，跳过本轮合成")
            return b"", self.default_format
        # 按本轮情绪挑参考音频；挑不到就回落 ref_audio + prompt_text
        ref_audio, ref_text = resolve_reference(self.config, emotion)

        if not ref_audio:
            logger.warning("GPT-SoVITS 未配置 ref_audio，跳过本轮合成")
            return b"", self.default_format
        if not ref_text:
            logger.warning("GPT-SoVITS 未配置 prompt_text，跳过本轮合成")
            return b"", self.default_format

        url = f"{self.config.base_url.rstrip('/')}/tts"
        payload = {
            "text": text,
            "text_lang": "zh",
            "ref_audio_path": ref_audio,
            "prompt_text": ref_text,
            "prompt_lang": "zh",
            "media_type": "wav",
            "streaming_mode": False,
            # 起伏的旋钮：默认等于引擎默认（音色最稳），
            # 想更生动就往 1.2~1.4 调，代价是音色会飘一点
            "temperature": self.config.temperature,
            "top_k": self.config.top_k,
            "top_p": self.config.top_p,
        }

        resp = await self.client.post(url, json=payload)

        if resp.status_code >= 400:
            # api_v2 的错误体是 {"message": "...", "Exception": "..."}，
            # 先把 body 摘要记下来，再交给基类归一化（否则只剩状态码，定位不了）
            logger.warning(
                "GPT-SoVITS 返回 %s：%s",
                resp.status_code,
                resp.text[:200],
            )
            resp.raise_for_status()

        return resp.content, "wav"
