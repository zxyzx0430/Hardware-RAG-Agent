"""On-demand PDF page rendering for source citations.

One page is rasterized to PNG per request and kept in a bounded in-memory LRU
cache. The cache key includes the document ID, page, scale and the source
file's size and mtime, so a replaced file is re-rendered instead of serving a
stale image. Nothing is rendered at startup.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MIN_SCALE = 0.5
MAX_SCALE = 2.0

# Safety caps for pathological page sizes. Scaling is reduced uniformly so the
# rendered bitmap never exceeds these bounds.
MAX_RENDER_SIDE = 4096
MAX_RENDER_PIXELS = 12_000_000

MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_CACHE_ENTRIES = 128


class PageImageError(Exception):
    """A page-image request that cannot be served; maps to the public error shape."""

    def __init__(
        self,
        code: str,
        status_code: int,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.message = message
        self.details = details


_cache_lock = threading.Lock()
# MuPDF is not reliably thread-safe, and one render per identical key is
# enough. Cache lookups are serialized with renders by this lock.
_render_lock = threading.Lock()
_cache: OrderedDict[tuple[str, int, str, int, int], bytes] = OrderedDict()
_cache_bytes = 0
_cache_hits = 0
_cache_misses = 0


def render_page_png(
    doc_id: str,
    pdf_path: Path,
    page: int,
    scale: float,
) -> tuple[bytes, bool]:
    """Render one 1-based PDF page; return ``(png_bytes, cache_hit)``.

    Raises :class:`PageImageError` for invalid input, a missing file, an
    out-of-range page or a rendering failure. Error messages never contain
    local absolute paths.
    """
    if not _valid_scale(scale):
        raise PageImageError(
            "INVALID_SCALE", 400, f"scale 必须在 {MIN_SCALE}-{MAX_SCALE} 之间",
        )
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise PageImageError("INVALID_PAGE", 400, "page 必须是从 1 开始的页码")

    try:
        stat = pdf_path.stat()
    except OSError:
        raise PageImageError("DOCUMENT_FILE_NOT_FOUND", 404, "文档原文件不存在") from None

    key = (doc_id, page, f"{scale:.3f}", stat.st_mtime_ns, stat.st_size)
    global _cache_hits, _cache_misses
    with _render_lock:
        with _cache_lock:
            cached = _cache.get(key)
            if cached is not None:
                _cache.move_to_end(key)
                _cache_hits += 1
                return cached, True
            _cache_misses += 1

        png = _render_page_bytes(pdf_path, page, scale)
        _store_in_cache(key, png)
        return png, False


def cache_info() -> dict[str, int]:
    """Return cache counters for observability and tests."""
    with _cache_lock:
        return {
            "entries": len(_cache),
            "bytes": _cache_bytes,
            "hits": _cache_hits,
            "misses": _cache_misses,
        }


def clear_cache() -> None:
    """Drop all cached images and counters."""
    global _cache_bytes, _cache_hits, _cache_misses
    with _cache_lock:
        _cache.clear()
        _cache_bytes = 0
        _cache_hits = 0
        _cache_misses = 0


def _valid_scale(scale: Any) -> bool:
    return (
        isinstance(scale, (int, float))
        and not isinstance(scale, bool)
        and math.isfinite(scale)
        and MIN_SCALE <= scale <= MAX_SCALE
    )


def _store_in_cache(key: tuple[str, int, str, int, int], png: bytes) -> None:
    global _cache_bytes
    with _cache_lock:
        if key in _cache:
            return
        _cache[key] = png
        _cache_bytes += len(png)
        while _cache and (
            len(_cache) > MAX_CACHE_ENTRIES or _cache_bytes > MAX_CACHE_BYTES
        ):
            _, evicted = _cache.popitem(last=False)
            _cache_bytes -= len(evicted)
        if key in _cache:
            _cache.move_to_end(key)


def _render_page_bytes(pdf_path: Path, page: int, scale: float) -> bytes:
    """Rasterize a single page with PyMuPDF. Kept module-level for tests."""
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover - dependency is pinned
        logger.exception("PyMuPDF is unavailable")
        raise PageImageError("PAGE_RENDER_FAILED", 500, "PDF 渲染组件不可用") from exc

    try:
        document = fitz.open(str(pdf_path))
    except Exception:
        logger.warning("Could not open PDF for page image rendering")
        raise PageImageError(
            "PAGE_RENDER_FAILED", 500, "PDF 文件无法打开或已损坏",
        ) from None

    try:
        page_count = document.page_count
        if page > page_count:
            raise PageImageError(
                "PAGE_OUT_OF_RANGE",
                400,
                f"页码超出范围：文档共 {page_count} 页",
                {"page": page, "page_count": page_count},
            )
        pdf_page = document[page - 1]
        zoom = _bounded_zoom(pdf_page.rect.width, pdf_page.rect.height, scale)
        pixmap = pdf_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pixmap.tobytes("png")
    except PageImageError:
        raise
    except Exception:
        logger.warning("PDF page rendering failed")
        raise PageImageError("PAGE_RENDER_FAILED", 500, "PDF 页面渲染失败") from None
    finally:
        document.close()


def _bounded_zoom(width_pt: float, height_pt: float, scale: float) -> float:
    """Clamp the requested scale to the maximum side and pixel budget."""
    zoom = scale
    if not math.isfinite(width_pt) or not math.isfinite(height_pt):
        return zoom
    if width_pt <= 0 or height_pt <= 0:
        return zoom

    # Keep a small margin: pixmap dimensions are rounded up, so an exact cap
    # could still overshoot by a pixel on each side.
    side_zoom = (MAX_RENDER_SIDE - 1) / max(width_pt, height_pt)
    pixel_zoom = math.sqrt((MAX_RENDER_PIXELS * 0.99) / (width_pt * height_pt))
    return max(0.05, min(zoom, side_zoom, pixel_zoom))
