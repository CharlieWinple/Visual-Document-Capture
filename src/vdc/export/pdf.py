"""Lossless page image export; screenshot DPI is a user convention."""
import io
import os
from pathlib import Path
import tempfile
import numpy as np
from PIL import Image
import pymupdf


def export_multipage_pdf(images, destination: str | Path, dpi: float = 96) -> int:
    if not np.isfinite(dpi) or dpi <= 0:
        raise ValueError("DPI must be positive and finite")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    temporary = None
    try:
        with pymupdf.open() as document:
            for pixels in images:
                if pixels.ndim != 3 or pixels.shape[2] != 3 or pixels.dtype != np.uint8:
                    raise ValueError("Expected RGB uint8 page")
                h, w = pixels.shape[:2]
                if not h or not w:
                    raise ValueError("Empty page")
                png = io.BytesIO()
                Image.fromarray(pixels).save(png, format="PNG")
                page = document.new_page(width=w * 72 / dpi, height=h * 72 / dpi)
                page.insert_image(page.rect, stream=png.getvalue())
                count += 1
            if not count:
                raise ValueError("No pages to export")
            handle, temporary = tempfile.mkstemp(suffix=".pdf", dir=destination.parent)
            os.close(handle)
            document.save(temporary, deflate=True)
        os.replace(temporary, destination)
        return count
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def export_session(store, pagination, destination, *, allow_uncertain=False, progress=None):
    if not allow_uncertain and (store.state.get("geometry_joins") or pagination.warnings or any(not p.complete for p in pagination.pages)):
        raise ValueError("Page boundaries require review before export")
    def images():
        for index, page in enumerate(pagination.pages):
            if progress:
                progress("编码导出页面", index, len(pagination.pages))
            yield store.read_rows(page.top, page.bottom)[:, page.left:page.right]
        if progress:
            progress("写入 PDF 文件", 0, 0)
    return export_multipage_pdf(images(), destination)
