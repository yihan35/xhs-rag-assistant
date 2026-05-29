"""
Local cover-image cache.

Xiaohongshu CDN image URLs can expire or reject hotlinking after sync. Cache the
cover while the URL is fresh, then serve the local file to the frontend.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COVERS_DIR = PROJECT_ROOT / "data" / "covers"

_SAFE_ID = re.compile(r"[^a-zA-Z0-9_.-]+")
_EXT_BY_TYPE = {
    "image/webp": ".webp",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
}
_KNOWN_EXTS = tuple(_EXT_BY_TYPE.values())


def _safe_note_id(note_id: str) -> str:
    safe = _SAFE_ID.sub("_", (note_id or "").strip())
    return safe or "cover"


def _existing_cached_url(note_id: str, covers_dir: Path) -> str | None:
    safe_id = _safe_note_id(note_id)
    for ext in _KNOWN_EXTS:
        if (covers_dir / f"{safe_id}{ext}").exists():
            return f"/covers/{safe_id}{ext}"
    return None


def _extension_from_response(url: str, content_type: str) -> str:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime in _EXT_BY_TYPE:
        return _EXT_BY_TYPE[mime]
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in _KNOWN_EXTS else ".webp"


def cache_cover_image(
    note_id: str,
    cover_url: str,
    covers_dir: Path = DEFAULT_COVERS_DIR,
    timeout: float = 12,
) -> str:
    """
    Download a remote cover image into data/covers and return its local URL.

    On failure, return the original URL so sync can continue. Existing cached
    covers win, which avoids re-downloading and keeps stable local URLs.
    """
    cover_url = (cover_url or "").strip()
    if not cover_url or not cover_url.startswith(("http://", "https://")):
        return cover_url

    covers_dir = Path(covers_dir)
    existing = _existing_cached_url(note_id, covers_dir)
    if existing:
        return existing

    try:
        resp = requests.get(
            cover_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.xiaohongshu.com/",
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        if not content_type.lower().startswith("image/") or not resp.content:
            raise ValueError(f"unexpected cover response content-type={content_type!r}")

        covers_dir.mkdir(parents=True, exist_ok=True)
        safe_id = _safe_note_id(note_id)
        ext = _extension_from_response(cover_url, content_type)
        path = covers_dir / f"{safe_id}{ext}"
        path.write_bytes(resp.content)
        logger.info(f"[{note_id}] 封面已缓存：{path}")
        return f"/covers/{path.name}"
    except Exception as exc:
        logger.warning(f"[{note_id}] 封面缓存失败，保留原 URL：{exc}")
        return cover_url
