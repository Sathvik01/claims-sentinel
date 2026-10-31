from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlmodel import Session, col, select

from app.db import get_session
from app.ingestion.service import DATASETS, IngestError, ingest_file
from app.models import IngestionBatch, IngestionError

router = APIRouter(prefix="/api/ingest", tags=["ingestion"])


@router.get("/datasets")
def datasets():
    out = []
    for name, (schema, _, pk) in DATASETS.items():
        out.append({"name": name, "primary_key": pk,
                    "fields": [{"name": f, "required": info.is_required()} for f, info in schema.model_fields.items()]})
    return out


@router.post("/{dataset}")
async def upload(dataset: str, file: UploadFile = File(...), session: Session = Depends(get_session)):
    if dataset != "bundle" and dataset not in DATASETS:
        raise HTTPException(404, f"Unknown dataset '{dataset}'")
    content = await file.read()
    try:
        batches = ingest_file(session, dataset, content, file.filename or "upload")
    except IngestError as e:
        raise HTTPException(422, str(e)) from e
    for b in batches:
        session.refresh(b)
    return [b.model_dump() for b in batches]


@router.get("/batches")
def batches(session: Session = Depends(get_session)):
    return session.exec(select(IngestionBatch).order_by(col(IngestionBatch.id).desc())).all()


@router.get("/batches/{batch_id}/errors")
def batch_errors(batch_id: int, session: Session = Depends(get_session)):
    return session.exec(select(IngestionError).where(IngestionError.batch_id == batch_id)
                        .order_by(col(IngestionError.row_number))).all()
