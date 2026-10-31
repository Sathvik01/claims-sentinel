from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.ai import llm
from app.config import settings
from app.db import get_session
from app.documents.extractor import InvoiceExtraction, extract, to_rows
from app.engine.runner import run_analysis
from app.ingestion.service import ingest_records
from app.models import Document, Finding, IngestionError

router = APIRouter(prefix="/api/documents", tags=["documents"])
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


@router.post("")
async def upload(file: UploadFile = File(...), session: Session = Depends(get_session)):
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
    return _run_extraction(doc, session)


@router.get("")
def list_documents(session: Session = Depends(get_session)):
    return session.exec(select(Document).order_by(col(Document.id).desc())).all()


@router.post("/{doc_id}/extract")
def re_extract(doc_id: int, session: Session = Depends(get_session)):
    doc = session.get(Document, doc_id) or _404()
    return _run_extraction(doc, session)


class ConfirmBody(BaseModel):
    extraction: InvoiceExtraction
    proposed_settlement: float | None = None


@router.post("/{doc_id}/confirm")
def confirm(doc_id: int, body: ConfirmBody, session: Session = Depends(get_session)):
    """Reviewed extraction → claim + lines through the normal validators → re-run the analysis."""
    doc = session.get(Document, doc_id) or _404()
    if doc.status == "CONFIRMED":
        raise HTTPException(409, f"Already confirmed as {doc.claim_id}")
    claim_id = f"CLM-DOC{doc.id:04d}"
    claim, lines = to_rows(body.extraction, claim_id, body.proposed_settlement, date.today().isoformat())
    cb = ingest_records(session, "claims", [claim], f"document:{doc.filename}", "document")
    errors = session.exec(select(IngestionError).where(IngestionError.batch_id == cb.id)).all()
    if cb.accepted == 0:
        return {"ok": False, "claim_id": None, "errors": [e.model_dump() for e in errors]}
    lb = ingest_records(session, "invoice_lines", lines, f"document:{doc.filename}", "document")
    errors += session.exec(select(IngestionError).where(IngestionError.batch_id == lb.id)).all()
    doc.status, doc.claim_id, doc.extraction = "CONFIRMED", claim_id, body.extraction.model_dump()
    session.add(doc)
    session.commit()
    run_analysis(session)
    findings = session.exec(select(Finding).where(Finding.claim_id == claim_id)).all()
    return {"ok": True, "claim_id": claim_id, "lines_accepted": lb.accepted, "lines_rejected": lb.rejected,
            "errors": [e.model_dump() for e in errors], "findings": [f.model_dump() for f in findings]}


def _404():
    raise HTTPException(404, "Document not found")
