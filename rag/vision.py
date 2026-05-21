"""
rag/vision.py
=============
用智谱 GLM-4.6v 替代传统 OCR，理解图片内容。

对每张图片调用一次 Vision API：
  - 提取图中文字
  - 生成内容描述

追加格式：
  [图片文字]: <提取的文字>
  [图片描述]: <内容描述>

结果不足 10 字则丢弃（通常是空图或纯装饰图）。
"""

import logging
import re

from .llm_config import zhipu_client, VISION_MODEL

logger = logging.getLogger(__name__)

_PROMPT = (
    "请提取图片中所有文字，并简短描述图片主要内容。"
    "格式：[文字]: xxx [描述]: xxx，"
    "如果没有文字只返回[描述]: xxx"
)

_MIN_CHARS = 10  # 结果低于此长度视为无效，丢弃


def _call_vision(image_url: str) -> str:
    """调用一次 Vision API，返回原始响应文本。失败返回空字符串。"""
    try:
        response = zhipu_client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text",      "text": _PROMPT},
                ],
            }],
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"Vision API 调用失败（{image_url[:60]}…）：{e}")
        return ""


def _parse_vision_result(raw: str) -> tuple[str, str]:
    """
    将 Vision API 返回的 '[文字]: xxx [描述]: xxx' 拆分为 (text, desc)。
    任一部分缺失时返回空字符串。
    """
    text_match = re.search(r"\[文字\]\s*[:：]\s*(.+?)(?=\s*\[描述\]|$)", raw, re.DOTALL)
    desc_match = re.search(r"\[描述\]\s*[:：]\s*(.+)", raw, re.DOTALL)

    text = text_match.group(1).strip() if text_match else ""
    desc = desc_match.group(1).strip() if desc_match else raw.strip()

    return text, desc


def extract_image_parts(
    image_urls: list[str],
    max_workers: int = 8,
) -> list[dict]:
    """
    对每张图片调用 Vision API，返回结构化 OCR / 图片描述结果。

    并发 8 路（官方限制 10 并发，保留 2 路余量）。
    可通过 max_workers 参数调整。

    单图失败时跳过，不中断整体流程。返回元素包含：
        url / ocr_text / description / raw
    结果顺序与输入 image_urls 一致（过滤空值后）。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    valid_urls = [(i, url) for i, url in enumerate(image_urls) if url]
    if not valid_urls:
        return []

    total = len(valid_urls)
    logger.info(f"  开始处理 {total} 张图片（并发 {min(max_workers, total)} 路）…")

    results: dict[int, dict] = {}
    import threading as _threading
    _done_count = 0
    _counter_lock = _threading.Lock()

    def _process(idx: int, url: str) -> tuple[int, dict | None]:
        nonlocal _done_count
        raw = _call_vision(url)
        with _counter_lock:
            _done_count += 1
            seq = _done_count          # 完成序号（1-based）
        if not raw or len(raw) < _MIN_CHARS:
            logger.info(f"  [图片 {seq}/{total}] 结果过短（{len(raw)} 字），跳过")
            return idx, None
        img_text, img_desc = _parse_vision_result(raw)
        if not img_text and not img_desc:
            logger.info(f"  [图片 {seq}/{total}] 无有效内容，跳过")
            return idx, None
        logger.info(f"  [图片 {seq}/{total}] ✓  {url[:60]}")
        return idx, {
            "url":         url,
            "ocr_text":    img_text,
            "description": img_desc,
            "raw":         raw.strip(),
        }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process, idx, url): idx for idx, url in valid_urls}
        for future in as_completed(futures):
            idx, result = future.result()
            if result is not None:
                results[idx] = result

    # 按原始顺序返回
    return [results[idx] for idx in sorted(results)]


def format_image_parts(image_parts: list[dict]) -> str:
    """将结构化图片结果格式化为写入 content 的文本。"""
    parts: list[str] = []
    for item in image_parts:
        line_parts: list[str] = []
        ocr_text = (item.get("ocr_text") or "").strip()
        description = (item.get("description") or "").strip()
        if ocr_text:
            line_parts.append(f"[图片文字]: {ocr_text}")
        if description:
            line_parts.append(f"[图片描述]: {description}")
        if line_parts:
            parts.append("\n".join(line_parts))

    return "\n".join(parts)


def extract_image_content(image_urls: list[str]) -> str:
    """
    对每张图片调用 Vision API，将结果拼接为追加到 content 字段的字符串。

    返回格式（多图时逐条追加）：
        [图片文字]: xxx
        [图片描述]: xxx
    """
    return format_image_parts(extract_image_parts(image_urls))
