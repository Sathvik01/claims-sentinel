"""Turns an uploaded document into page images the forensic skills can work on."""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader

from app.config import settings

MAX_PAGES = 3
RENDER_DPI = 150


@dataclass
class PageImage:
    page: int
    image: Image.Image  # RGB
    path: Path  # cached PNG (what the UI shows)
    raster_source: bool  # True for photos/scans; False for pages rendered from a digital PDF
    original_bytes: bytes | None  # the uploaded JPEG/PNG bytes, kept for error-level analysis


def _cache_dir() -> Path:
    d = settings.upload_dir / "pages"
    d.mkdir(parents=True, exist_ok=True)
    return d


def pdf_has_text(data: bytes) -> bool:
    reader = PdfReader(io.BytesIO(data))
    return sum(len((p.extract_text() or "").strip()) for p in reader.pages[:MAX_PAGES]) >= 40


def page_images(key: str, data: bytes, content_type: str) -> list[PageImage]:
    """Page PNGs are cached by content hash (not by `key`): document ids restart after a database reset,
    and an id-keyed cache would hand a new upload an old document's pages and image features."""
    key = f"{key.split('-')[0]}-{hashlib.sha1(data).hexdigest()[:16]}"
    out: list[PageImage] = []
    if content_type == "application/pdf":
        digital = pdf_has_text(data)
        pdf = pdfium.PdfDocument(data)
        for i in range(min(len(pdf), MAX_PAGES)):
            img = pdf[i].render(scale=RENDER_DPI / 72).to_pil().convert("RGB")
            path = _cache_dir() / f"{key}_p{i}.png"
            if not path.exists():
                img.save(path)
            out.append(PageImage(i, img, path, raster_source=not digital, original_bytes=None))
        pdf.close()
    else:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        path = _cache_dir() / f"{key}_p0.png"
        if not path.exists():
            img.save(path)
        out.append(PageImage(0, img, path, raster_source=True, original_bytes=data))
    return out
