"""
rag/embedder.py
===============
智谱 embedding-3 向量化，封装为 ChromaDB EmbeddingFunction。

ChromaDB 要求 EmbeddingFunction 的 __call__ 接收 list[str] 并返回 list[list[float]]。
智谱 API 支持批量 input，一次请求处理整个 batch，避免 N 次单条调用。
"""

import logging
from chromadb import EmbeddingFunction, Embeddings  # type: ignore

from .llm_config import zhipu_client, EMBEDDING_MODEL

logger = logging.getLogger(__name__)


class ZhipuEmbeddingFunction(EmbeddingFunction):
    """
    ChromaDB 自定义嵌入函数，底层调用智谱 embedding-3 API。

    维度：2048（embedding-3 默认值）
    距离度量：余弦相似度（在 ChromaStore 初始化时指定 hnsw:space=cosine）
    """

    def __call__(self, input: list[str]) -> Embeddings:
        if zhipu_client is None:
            raise EnvironmentError("ZHIPUAI_API_KEY 未设置，无法调用 Embedding API")

        if not input:
            return []

        try:
            response = zhipu_client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=input,
            )
            # 按 index 排序，保证顺序与输入一致
            items = sorted(response.data, key=lambda x: x.index)
            return [item.embedding for item in items]

        except Exception as e:
            logger.error(f"智谱 Embedding API 调用失败：{e}")
            raise


def embed_text(text: str) -> list[float]:
    """
    单条文本向量化工具函数（供非 ChromaDB 场景使用）。
    """
    ef = ZhipuEmbeddingFunction()
    return ef([text])[0]
