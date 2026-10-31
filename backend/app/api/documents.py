from datetime import date
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.ai import llm
from app.config import settings
from app.auth.deps import require
from app.db import get_session
from app.documents.evidence import fields_from_llm, page_texts, store
from app.documents.extractor import InvoiceExtraction, extract, to_rows
from app.engine.runner import run_analysis
from app.forensics.agent import STAGES, run_scan
from app.forensics.render import page_images
from app.ingestion.service import ingest_records
from app.models import Document, DocumentScan, Finding, ImageFingerprint, IngestionError

router = APIRouter(prefix="/api", tags=["documents"])
VIEW, MANAGE = Depends(require("documents:view")), Depends(require("documents:manage"))
MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _run_extraction(doc: Document, session: Session) -> Document:
    try:
        ext = extract(Path(doc.path).read_bytes(), doc.content_type)
        doc.extraction, doc.status, doc.error = ext.model_dump(), "EXTRACTED", None
    except llm.LLMUnavailable as e:
        doc.status, doc.error = "FAILED", f"{e}. Set GROQ_API_KEY in backend/.env to enable document extraction."
    except Exception as e:
        doc.status, doc.error = "FAILED", f"Extraction failed: {e}"
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


def _start_scan(doc: Document, session: Session, tasks: BackgroundTasks) -> DocumentScan:
    scan = DocumentScan(doc_id=doc.id)
    session.add(scan)
    session.commit()
    session.refresh(scan)
    tasks.add_task(run_scan, scan.id)
    return scan


def _latest_scan(session: Session, doc_id: int) -> DocumentScan | None:
    return session.exec(select(DocumentScan).where(DocumentScan.doc_id == doc_id)
                        .order_by(col(DocumentScan.id).desc())).first()


def _scan_view(scan: DocumentScan | None) -> dict | None:
    if scan is None:
        return None
    done = {s["tool"] for s in scan.steps if s["status"] == "done"}
    progress = 1.0 if scan.status in ("DONE", "FAILED") else round(len(done & set(STAGES)) / len(STAGES), 2)
    return scan.model_dump() | {"progress": progress, "stages": STAGES}


@router.post("/documents", dependencies=[MANAGE])
async def upload(tasks: BackgroundTasks, file: UploadFile = File(...), session: Session = Depends(get_session)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in MIME:
        raise HTTPException(422, "Upload a PDF, PNG or JPG document")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    doc = Document(filename=file.filename, content_type=MIME[ext], path="")
    session.add(doc)
    session.commit()
    path = settings.upload_dir / f"{doc.id:05d}{ext}"
    path.write_bytes(await file.read())
    doc.path = str(path)
    out = _run_extraction(doc, session).model_dump()
    _start_scan(doc, session, tasks)  # integrity agent runs after the response is sent
    return out


@router.get("/documents", dependencies=[VIEW])
def list_documents(session: Session = Depends(get_session)):
    docs = session.exec(select(Document).order_by(col(Document.id).desc())).all()
    out = []
    for d in docs:
        s = _latest_scan(session, d.id)
        out.append(d.model_dump() | {"scan": None if s is None else
                                     {"id": s.id, "status": s.status, "level": s.level, "score": s.score}})
    return out


@router.post("/documents/{doc_id}/extract", dependencies=[MANAGE])
def re_extract(doc_id: int, session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id) or _404()
    return _run_extraction(doc, session)


@router.post("/documents/{doc_id}/scan", dependencies=[MANAGE])
def start_scan(doc_id: int, tasks: BackgroundTasks, session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id) or _404()
    return _scan_view(_start_scan(doc, session, tasks))


@router.get("/documents/{doc_id}/scan", dependencies=[VIEW])
def get_scan(doc_id: int, session: Session = Depends(get_session)):
    return _scan_view(_latest_scan(session, doc_id))


@router.get("/documents/{doc_id}/pages/{page}", dependencies=[VIEW])
def page_image(doc_id: int, page: int, session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id) or _404()
    pages = page_images(f"doc{doc.id}", Path(doc.path).read_bytes(), doc.content_type)
    if not 0 <= page < len(pages):
        raise HTTPException(404, "No such page")
    return FileResponse(pages[page].path, media_type="image/png")


@router.get("/scans/{scan_id}/ela/{page}", dependencies=[VIEW])
def ela_heatmap(scan_id: int, page: int):
    path = settings.upload_dir / "scans" / f"scan{scan_id}_ela_p{page}.png"
    if not path.exists():
        raise HTTPException(404, "No heatmap for this page")
    return FileResponse(path, media_type="image/png")


@router.get("/fingerprints/{fp_id}/image", dependencies=[VIEW])
def fingerprint_image(fp_id: int, session: Session = Depends(get_session)):
    fp = session.get(ImageFingerprint, fp_id)
    if not fp or not Path(fp.image_path).exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(fp.image_path, media_type="image/png")


class ConfirmBody(BaseModel):
    extraction: InvoiceExtraction
    proposed_settlement: float | None = None


@router.post("/documents/{doc_id}/confirm", dependencies=[MANAGE])
def confirm(doc_id: int, body: ConfirmBody, session: Session = Depends(get_session)):
    """Reviewed extraction → claim + lines through the normal validators → re-run the analysis."""
    doc = session.get(Document, doc_id) or _404()
    if doc.status == "CONFIRMED":
        raise HTTPException(409, f"Already confirmed as {doc.claim_id}")
    claim_id = f"CLM-DOC{doc.id:04d}"
    claim, lines = to_rows(body.extraction, claim_id, body.proposed_settlement, date.today().isoformat())
    cb = ingest_records(session, "claims", [claim], f"document:{doc.filename}", "document", source="document")
    errors = session.exec(select(IngestionError).where(IngestionError.batch_id == cb.id)).all()
    if cb.accepted == 0:
        return {"ok": False, "claim_id": None, "errors": [e.model_dump() for e in errors]}
    lb = ingest_records(session, "invoice_lines", lines, f"document:{doc.filename}", "document")
    errors += session.exec(select(IngestionError).where(IngestionError.batch_id == lb.id)).all()
    doc.status, doc.claim_id, doc.extraction = "CONFIRMED", claim_id, body.extraction.model_dump()
    session.add(doc)
    for fp in session.exec(select(ImageFingerprint).where(ImageFingerprint.doc_id == doc.id)):
        fp.claim_id = claim_id  # later resubmissions of this bill now point at this claim
        session.add(fp)
    attach_llm_evidence(session, doc, body.extraction.model_dump())
    session.commit()
    run_analysis(session)
    findings = session.exec(select(Finding).where(Finding.claim_id == claim_id)).all()
    return {"ok": True, "claim_id": claim_id, "lines_accepted": lb.accepted, "lines_rejected": lb.rejected,
            "errors": [e.model_dump() for e in errors], "findings": [f.model_dump() for f in findings]}


def attach_llm_evidence(session: Session, doc: Document, extraction: dict) -> None:
    """Store the reviewed extraction as evidence fields, each verified by locating it in the PDF text layer."""
    doc.doc_type = {"invoice": "INVOICE", "discharge_summary": "DISCHARGE_SUMMARY"}.get(
        extraction.get("document_type") or "", "OTHER")
    texts = page_texts(doc.path) if doc.content_type == "application/pdf" else []
    method = "text-llm" if texts and any(t.strip() for t in texts) else "vision-llm"
    store(session, doc, fields_from_llm(extraction, texts), method, settings.groq_model if method == "text-llm"
          else settings.groq_vision_model)


def _404():
    raise HTTPException(404, "Document not found")
