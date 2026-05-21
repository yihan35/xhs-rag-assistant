"""
RawNote 数据模型 —— 爬虫层输出 schema
存储层由其他模块负责，此处只做结构定义
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RawNote:
    note_id: str
    title: str
    content: str          # 正文 + OCR文字 + 视频转录，全部合并
    tags: list[str]
    note_url: str
    cover_url: str        # 封面图 URL，前端展示
    image_urls: list[str] # 其他图片 URL
    likes: int
    note_type: str        # "image" | "video"
    crawled_at: str
    content_parts: dict = field(default_factory=dict)  # 正文 / OCR / 图片描述 / 视频转录的结构化拆分
    note_published_at: str = ""  # 帖子发布时间（ISO 格式），从小红书 API time 字段转换

    def to_dict(self) -> dict:
        return {
            "note_id":          self.note_id,
            "title":            self.title,
            "content":          self.content,
            "content_parts":    self.content_parts,
            "tags":             self.tags,
            "note_url":         self.note_url,
            "cover_url":        self.cover_url,
            "image_urls":       self.image_urls,
            "likes":            self.likes,
            "note_type":        self.note_type,
            "crawled_at":       self.crawled_at,
            "note_published_at": self.note_published_at,
        }
