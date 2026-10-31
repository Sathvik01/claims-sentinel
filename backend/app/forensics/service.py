"""Skill implementations bound to a stored document, plus the fingerprint store."""

from __future__ import annotations

import io
from dataclasses import asdict
from pathlib import Path

from PIL import Image
from sqlmodel import Session, select

from app.config import settings
from app.forensics import duplicates as dup
from app.forensics.copy_move import detect_copy_move
from app.forensics.ela import error_level_analysis
from app.forensics.render import PageImage, page_images
from app.models import Document, ImageFingerprint

SHORTLIST = 60  # pHash cannot rank same-template bills, so the shortlist must be generous
SAME_TEMPLATE_INK = 0.93


def scan_dir() -> Path:
    d = settings.upload_dir / "scans"
    d.mkdir(parents=True, exist_ok=True)
    return d


class DocumentPages:
    """Lazily rendered pages of one uploaded document."""

    def __init__(self, doc: Document):
        self.doc = doc
        self._pages: list[PageImage] | None = None

    @property
    def pages(self) -> list[PageImage]:
        if self._pages is None:
            self._pages = page_images(f"doc{self.doc.id}", Path(self.doc.path).read_bytes(), self.doc.content_type)
        return self._pages

    def inspect(self) -> dict:
        p0 = self.pages[0]
        jpeg = bool(p0.original_bytes and p0.original_bytes[:3] == b"\xff\xd8\xff")
        return {
            "doc_id": self.doc.id, "filename": self.doc.filename, "content_type": self.doc.content_type,
            "pages": len(self.pages), "width": p0.image.width, "height": p0.image.height,
            "raster_source": p0.raster_source, "jpeg": jpeg,
            "notes": ("Photo/scan: compression traces available, so error-level analysis is meaningful." if jpeg
                      else "Digital PDF: pages are rendered from vectors; error-level analysis is not meaningful, "
                           "duplicate and clone checks still apply." if not p0.raster_source
                      else "Lossless raster image: error-level analysis has no JPEG traces to use."),
        }


def register_fingerprints(session: Session, doc: Document, pages: list[PageImage]) -> None:
    if session.exec(select(ImageFingerprint).where(ImageFingerprint.doc_id == doc.id)).first():
        return
    for p in pages:
        fp = dup.fingerprint(p.image)
        session.add(ImageFingerprint(doc_id=doc.id, claim_id=doc.claim_id, page=p.page, phash=fp.phash,
                                     dhash=fp.dhash, image_path=str(p.path), label=doc.filename, source="upload"))
    session.commit()


def find_duplicates(session: Session, dp: DocumentPages) -> dict:
    register_fingerprints(session, dp.doc, dp.pages)
    others = session.exec(select(ImageFingerprint).where(ImageFingerprint.doc_id != dp.doc.id)).all() + \
        session.exec(select(ImageFingerprint).where(ImageFingerprint.doc_id.is_(None))).all()
    duplicates, same_template, checked = [], [], 0
    for p in dp.pages:
        fq = dup.fingerprint(p.image)
        shortlist = sorted(others, key=lambda o: dup.hash_distance(fq.phash, o.phash))[:SHORTLIST]
        for o in shortlist:
            cand = Image.open(o.image_path).convert("RGB")
            r = dup.compare(p.image, cand, str(p.path), o.image_path)
            checked += 1
            entry = {"page": p.page, "fingerprint_id": o.id, "doc_id": o.doc_id, "claim_id": o.claim_id,
                     "label": o.label, "source": o.source, "image_path": o.image_path,
                     "hash_distance": dup.hash_distance(fq.phash, o.phash), **r}
            if r["ink_agreement"] >= dup.SAME_DOC_INK:
                duplicates.append(entry)
            elif r["ink_agreement"] >= SAME_TEMPLATE_INK:
                same_template.append(entry)
    duplicates.sort(key=lambda d: (d["claim_id"] is None, -d["ink_agreement"]))  # paid claims first
    return {"compared": checked, "store_size": len(others), "duplicates": duplicates,
            "same_template": same_template[:3]}


def run_ela(dp: DocumentPages, scan_id: int, page: int) -> dict:
    p = dp.pages[page]
    r = error_level_analysis(p.image, p.original_bytes, p.raster_source,
                             scan_dir() / f"scan{scan_id}_ela_p{page}.png")
    return {"page": page, **asdict(r)}


def run_copy_move(dp: DocumentPages, page: int) -> dict:
    return {"page": page, **asdict(detect_copy_move(dp.pages[page].image))}


def seed_fingerprints(session: Session, seed_dir: Path) -> int:
    """Fingerprint bills already paid (sample_data/documents/seed/{claim_id}.jpg) as the historical store."""
    n = 0
    for f in sorted(seed_dir.glob("*.jpg")):
        data = f.read_bytes()
        p = page_images(f"seed-{f.stem}", data, "image/jpeg")[0]
        fp = dup.fingerprint(Image.open(io.BytesIO(data)).convert("RGB"))
        session.add(ImageFingerprint(claim_id=f.stem, page=0, phash=fp.phash, dhash=fp.dhash,
                                     image_path=str(p.path), label=f"Paid bill of {f.stem}", source="seed"))
        n += 1
    session.commit()
    return n
