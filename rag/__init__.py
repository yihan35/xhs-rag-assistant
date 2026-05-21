"""
rag
===
RAG 能力层，统一封装四种 AI 能力：

  embedder    — 智谱 embedding-3，向量化笔记 content
  vision      — 智谱 GLM-4.6v，图片文字提取 + 内容描述
  transcriber — OpenAI Whisper-1，视频语音转录
  chat        — 智谱 GLM-5.1，召回原文后生成总结/回答

所有客户端从 rag.llm_config 统一初始化，不在各子模块重复创建。
"""

from .embedder    import ZhipuEmbeddingFunction, embed_text
from .vision      import extract_image_content
from .transcriber import transcribe_video
from .chat        import analyze

__all__ = [
    "ZhipuEmbeddingFunction",
    "embed_text",
    "extract_image_content",
    "transcribe_video",
    "analyze",
]
