"""
rag/llm_config.py
=================
统一 AI 客户端配置。所有 RAG 模块从此处 import，不各自初始化。

依赖：
    pip install zhipuai openai python-dotenv

环境变量（写在 .env 文件或系统环境中）：
    ZHIPUAI_API_KEY   — 智谱 AI API key（embedding / chat / vision）
    OPENAI_API_KEY    — OpenAI API key（仅 Whisper 视频转录用）
"""

import os
from dotenv import load_dotenv  # type: ignore

# 优先从 .env 文件加载，不覆盖已有的系统环境变量
load_dotenv(override=False)

# ── 智谱 AI 客户端（embedding / chat / vision）─────────────────────
from zhipuai import ZhipuAI  # type: ignore

_zhipu_key = os.getenv("ZHIPUAI_API_KEY", "")
if not _zhipu_key:
    import warnings
    warnings.warn(
        "ZHIPUAI_API_KEY 未设置，RAG 功能（向量化/问答/图片理解）将不可用。"
        "如需使用，请在 .env 中添加：ZHIPUAI_API_KEY=your_key",
        RuntimeWarning,
        stacklevel=2,
    )
    zhipu_client = None  # type: ignore[assignment]
else:
    zhipu_client = ZhipuAI(api_key=_zhipu_key)

# ── OpenAI 客户端（仅 Whisper 转录用）─────────────────────────────
from openai import OpenAI  # type: ignore

_openai_key = os.getenv("OPENAI_API_KEY", "")
# 不强制要求：只在用到视频转录时才会失败
whisper_client = OpenAI(api_key=_openai_key) if _openai_key else None

# ── 模型常量 ───────────────────────────────────────────────────────
EMBEDDING_MODEL = "embedding-3"  # 智谱向量化，2048 维
CHAT_MODEL      = "glm-5.1"      # 智谱对话/总结
VISION_MODEL    = "glm-4.6v"     # 智谱图片理解
