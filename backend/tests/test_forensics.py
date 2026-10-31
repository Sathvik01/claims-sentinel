"""Forensic skills on the generated sample images, the score, and the scan pipeline without an LLM."""

import shutil
from pathlib import Path

import pytest
from PIL import Image
from sqlmodel import Session

from app.config import settings
from app.forensics import duplicates as dup
from app.forensics.copy_move import detect_copy_move
from app.forensics.ela import error_level_analysis
from app.forensics.score import fabrication_score

DOCS = settings.sample_data_dir / "documents"
IMG = DOCS / "images"


def _ela(name: str, tmp_path: Path):
    data = (IMG / name).read_bytes()
    return error_level_analysis(Image.open(IMG / name).convert("RGB"), data, True, tmp_path / f"{name}.png")


def _overlaps(h: dict, box: tuple[float, float, float, float], w: int, hgt: int) -> bool:
    x0, y0, x1, y1 = box[0] * w, box[1] * hgt, box[2] * w, box[3] * hgt
    return h["x"] < x1 and h["x"] + h["w"] > x0 and h["y"] < y1 and h["y"] + h["h"] > y0


def test_ela_flags_edited_amounts_only(tmp_path):
    clean = _ela("bill_genuine.jpg", tmp_path)
    tampered = _ela("bill_tampered_amount.jpg", tmp_path)
    assert clean.applicable and not clean.hotspots
    assert len(tampered.hotspots) >= 2
    w, h = Image.open(IMG / "bill_tampered_amount.jpg").size
    edited = [(0.80, 0.555, 0.95, 0.575), (0.805, 0.4255, 0.925, 0.4405)]  # see forensic_samples.py
    for box in edited:
        assert any(_overlaps(hs, box, w, h) for hs in tampered.hotspots), box


def test_ela_not_applicable_to_digital_pdf(tmp_path):
    r = error_level_analysis(Image.new("RGB", (100, 100), "white"), None, False, tmp_path / "x.png")
    assert not r.applicable


def test_copy_move_finds_clone_and_ignores_genuine():
    assert detect_copy_move(Image.open(IMG / "bill_cloned_stamp.jpg").convert("RGB")).clusters
    assert not detect_copy_move(Image.open(IMG / "bill_genuine.jpg").convert("RGB")).clusters


def test_duplicates_match_reused_copies_but_not_same_template_bills():
    paid = Image.open(DOCS / "seed" / "CLM-000048.jpg").convert("RGB")
    for name in ("bill_reused_resized.jpg", "bill_reused_cropped.jpg"):
        r = dup.compare(Image.open(IMG / name).convert("RGB"), paid)
        assert r["ink_agreement"] >= dup.SAME_DOC_INK, (name, r)
    genuine = Image.open(IMG / "bill_genuine.jpg").convert("RGB")
    for seed in (DOCS / "seed").glob("*.jpg"):
        r = dup.compare(genuine, Image.open(seed).convert("RGB"))
        assert r["ink_agreement"] < dup.SAME_DOC_INK, (seed.name, r)


def test_score_combination_and_skipped_skills():
    none = fabrication_score({})
    assert none["score"] == 0 and none["level"] == "LOW" and len(none["skipped"]) == 3
    dup_only = fabrication_score({"find_duplicate_images": {"duplicates": [
        {"ink_agreement": 0.999, "scale": 1.0, "claim_id": "CLM-1"}]}})
    assert dup_only["score"] == 90 and dup_only["level"] == "HIGH"
    both = fabrication_score({
        "find_duplicate_images": {"duplicates": []},
        "error_level_analysis": [{"page": 0, "applicable": True, "hotspots": [{}, {}], "anomalous_fraction": 0.01}],
        "detect_copy_move": [{"page": 0, "clusters": [{"pairs": 50, "offset": [100, 100]}]}],
    })
    assert both["score"] > max(c["score"] or 0 for c in both["contributions"])  # signals compound


@pytest.mark.slow
def test_scan_pipeline_without_llm(tmp_path):
    from app.db import engine, init_db
    from app.forensics.agent import run_scan
    from app.forensics.service import seed_fingerprints
    from app.models import Document, DocumentScan

    init_db()
    with Session(engine) as s:
        seed_fingerprints(s, DOCS / "seed")
        dst = settings.upload_dir / "reused.jpg"
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(IMG / "bill_reused_resized.jpg", dst)
        doc = Document(filename="reused.jpg", content_type="image/jpeg", path=str(dst))
        s.add(doc)
        s.commit()
        scan = DocumentScan(doc_id=doc.id)
        s.add(scan)
        s.commit()
        run_scan(scan.id)
        s.refresh(scan)
    assert scan.status == "DONE" and scan.mode == "fixed pipeline"
    assert scan.level == "HIGH", scan.results.get("find_duplicate_images")
    assert [st["tool"] for st in scan.steps][:2] == ["inspect_document", "find_duplicate_images"]
    assert scan.results["find_duplicate_images"]["duplicates"][0]["claim_id"] == "CLM-000048"
