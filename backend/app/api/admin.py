from fastapi import APIRouter, Depends
from sqlmodel import Session, col, select

from app.ai import llm
from app.config import settings
from app.db import engine, get_session, reset_db
from app.engine.base import CATEGORY_LABELS
from app.engine.runner import run_analysis
from app.ingestion.service import load_sample_dir
from app.models import AnalysisRun, Claim, Provider
from app.synthetic.evaluate import evaluate

router = APIRouter(prefix="/api", tags=["admin"])


@router.post("/analysis/run")
def run(session: Session = Depends(get_session)):
    return run_analysis(session)


@router.get("/analysis/latest")
def latest(session: Session = Depends(get_session)):
    return session.exec(select(AnalysisRun).order_by(col(AnalysisRun.id).desc())).first()


@router.post("/demo/load-sample")
def load_sample():
    """Resets the database, ingests sample_data/*.csv through the normal validators, runs the analysis."""
    engine.dispose()
    reset_db()
    with Session(engine) as session:
        batches = load_sample_dir(session, settings.sample_data_dir)
        r = run_analysis(session)
        for b in batches:
            session.refresh(b)
        return {"batches": [b.model_dump() for b in batches], "run": r.model_dump()}


@router.post("/reset")
def reset():
    engine.dispose()
    reset_db()
    return {"ok": True}


@router.get("/evaluation")
def evaluation(session: Session = Depends(get_session)):
    return evaluate(session)


@router.get("/meta")
def meta(session: Session = Depends(get_session)):
    types = sorted(set(session.exec(select(Claim.claim_type).where(Claim.status == "PENDING"))))
    return {
        "llm_enabled": llm.available(), "llm_provider": llm.PROVIDER, "llm_model": settings.groq_model,
        "llm_vision_model": settings.groq_vision_model,
        "claim_types": types,
        "providers": [{"provider_id": p.provider_id, "name": p.name}
                      for p in session.exec(select(Provider).order_by(col(Provider.name)))],
        "categories": [{"value": k, "label": v} for k, v in CATEGORY_LABELS.items()],
        "severities": ["HIGH", "MEDIUM", "LOW"],
        "statuses": ["OPEN", "CONFIRMED", "CORRECTED", "DISMISSED"],
        "finding_types": ["CONFIRMED_VIOLATION", "SUSPECTED_PATTERN", "NEEDS_INFO"],
    }
