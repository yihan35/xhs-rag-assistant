"""
rag/indexer.py
==============
建库：文本分块 + 向量化 + 写入 ChromaDB。
负责人：李奕涵

对外接口（后端调用）：
    index_note(note: dict, user_id: str) -> bool

内部依赖：
    - rag.storage.NoteStore    写 SQLite + ChromaDB
    - rag.llm_config       客户端 + 模型常量

TODO（李奕涵）：
    [ ] 接入 LangChain RecursiveCharacterTextSplitter，长文本分块
    [ ] chunk_size=500 / chunk_overlap=50（视频转录帖子才需切分）
    [ ] 支持 folder_id 字段写入 ChromaDB metadata
    [ ] 批量入库接口 index_notes(notes, user_id)
"""

import logging
from rag.storage import NoteStore

logger = logging.getLogger(__name__)


def index_note(note: dict, user_id: str) -> bool:
    """
    将一条 RawNote.to_dict() 结果向量化存入 ChromaDB，元数据写入 SQLite。

    参数：
        note      RawNote.to_dict() 的结果，需含 content / title / note_id 等字段
        user_id   小红书用户 ID，用于多租户隔离

    返回：
        True  = 新记录
        False = 已存在（已更新）
    """
    with NoteStore() as store:
        return store.save(note, user_id=user_id)


def index_notes(notes: list[dict], user_id: str) -> dict:
    """
    批量入库，返回统计信息。

    返回：{"new": int, "updated": int, "failed": int}
    """
    new = updated = failed = 0
    with NoteStore() as store:
        for note in notes:
            try:
                is_new = store.save(note, user_id=user_id)
                if is_new:
                    new += 1
                else:
                    updated += 1
            except Exception as e:
                logger.error(f"[{note.get('note_id')}] 入库失败：{e}")
                failed += 1
    return {"new": new, "updated": updated, "failed": failed}
