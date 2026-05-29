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


def reindex_all(user_id: str) -> dict:
    """
    对指定用户的所有在库笔记重新向量化（用于 embedding 策略变更后的存量迁移）。

    流程：将所有 is_collected=1 的笔记的 indexed 标记置 0，
    然后重新 upsert 到 ChromaDB（自动使用最新的 document 构建策略）。

    返回：{"reindexed": int, "failed": int}
    """
    from rag.storage import NoteStore

    reindexed = failed = 0
    with NoteStore() as store:
        notes = store.sqlite.all_notes(user_id=user_id)
        logger.info(f"[reindex_all] 共 {len(notes)} 条笔记待重建索引，user_id={user_id}")
        for note in notes:
            try:
                content = (note.get("content") or "").strip()
                if not content:
                    logger.debug(f"[{note['note_id']}] content 为空，跳过")
                    continue
                store.chroma.upsert(
                    note_id=note["note_id"],
                    content=content,
                    user_id=user_id,
                    title=note.get("title", ""),
                )
                store.sqlite.mark_indexed(note["note_id"], user_id)
                reindexed += 1
            except Exception as e:
                logger.error(f"[{note.get('note_id')}] 重建索引失败：{e}")
                failed += 1

    logger.info(f"[reindex_all] 完成：reindexed={reindexed}, failed={failed}")
    return {"reindexed": reindexed, "failed": failed}
