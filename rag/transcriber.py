"""
rag/transcriber.py
==================
用 OpenAI Whisper-1 转录视频语音为文字。

流程：
  1. 用 ffmpeg 从视频 URL 下载并提取 16kHz 单声道音频（.mp3）
  2. 调用 OpenAI whisper-1 API 转录为中文文本

失败时返回空字符串，不中断主流程。
依赖：ffmpeg 需在系统 PATH 中可用（brew install ffmpeg）
"""

import logging
import os
import subprocess
import tempfile
import urllib.request

from .llm_config import whisper_client

logger = logging.getLogger(__name__)


def transcribe_video(video_url: str) -> str:
    """
    下载视频 → 提取音频 → Whisper 转录，返回转录文本。
    失败时返回空字符串。
    """
    if not video_url:
        return ""

    if whisper_client is None:
        logger.warning("OPENAI_API_KEY 未配置，跳过视频转录")
        return ""

    tmp_video = tmp_audio = ""
    try:
        # ── 1. 下载视频到临时文件 ─────────────────────────────────
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as vf:
            tmp_video = vf.name
        logger.debug(f"下载视频：{video_url[:60]}…")
        urllib.request.urlretrieve(video_url, tmp_video)

        # ── 2. ffmpeg 提取音频（16kHz 单声道 mp3）────────────────
        tmp_audio = tmp_video.replace(".mp4", ".mp3")
        result = subprocess.run(
            [
                "ffmpeg", "-i", tmp_video,
                "-ar", "16000",
                "-ac", "1",
                "-vn",           # 不要视频流
                tmp_audio, "-y",
            ],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning(
                f"ffmpeg 失败（code={result.returncode}）：{result.stderr[:200]}"
            )
            return ""

        # ── 3. Whisper 转录 ───────────────────────────────────────
        logger.debug("调用 Whisper API 转录中…")
        with open(tmp_audio, "rb") as f:
            transcript = whisper_client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="zh",
            )
        text = (transcript.text or "").strip()
        logger.info(f"视频转录完成（{len(text)} 字）")
        return text

    except FileNotFoundError:
        logger.warning("未找到 ffmpeg，跳过视频转录。请安装：brew install ffmpeg")
        return ""
    except Exception as e:
        logger.warning(f"视频转录失败：{e}")
        return ""
    finally:
        for path in (tmp_video, tmp_audio):
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception:
                    pass
