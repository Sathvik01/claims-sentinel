from fastapi import APIRouter, Depends
from sqlalchemy import update
from sqlmodel import Session, col, select

from app import audit
from app.ai import llm
from app.auth.deps import CurrentUser, require
from app.auth.seed import seed_policyholders, seed_staff
from app.config import settings
from app.db import engine, get_session, reset_db
from app.engine.base import CATEGORY_LABELS
from app.engine.runner import run_analysis
from app.documents.evidence import load_claim_evidence
from app.forensics.service import seed_fingerprints
from app.ingestion.service import load_sample_dir
from app.models import AnalysisRun, Claim, Provider, User
from app.workflow.lifecycle import route_after_analysis
from app.synthetic.evaluate import evaluate

router = APIRouter(prefix="/api", tags=["admin"])


def analyse_and_route(session: Session) -> tuple[AnalysisRun, dict]:
    r = run_analysis(session)
    moved = route_after_analysis(session)
    session.commit()
    return r, moved


@router.post("/analysis/run")
def run(session: Session = Depends(get_session), user: User = Depends(require("analysis:run"))):
    audit.record(session, action="ANALYSIS_REQUESTED", entity="analysis", actor=user)
    r, moved = analyse_and_route(session)
    return r.model_dump() | {"routed": moved}


@router.get("/analysis/latest")
def latest(_: CurrentUser, session: Session = Depends(get_session)):
    return session.exec(select(AnalysisRun).order_by(col(AnalysisRun.id).desc())).first()


@router.post("/demo/load-sample", dependencies=[Depends(require("data:ingest"))])
def load_sample():
    """Resets the database, ingests sample_data/*.csv through the normal validators, seeds demo users and the
    paid-bill image store, runs the analysis and routes claims into the work queues."""
    engine.dispose()
    reset_db()
    with Session(engine) as session:
        seed_staff(session)
        batches = load_sample_dir(session, settings.sample_data_dir)
        session.exec(update(Claim).where(Claim.status == "PAID").values(state="SETTLED"))
        session.commit()
        holders = seed_policyholders(session)
        seeded = seed_fingerprints(session, settings.sample_data_dir / "documents" / "seed")
        evidence = load_claim_evidence(session, settings.sample_data_dir / "documents" / "claims")
        audit.record(session, action="DEMO_DATA_LOADED", entity="analysis", actor="system:demo-loader",
                     after={"batches": len(batches), "policyholders": holders, "seed_documents": seeded,
                            "evidence_documents": evidence})
        r, moved = analyse_and_route(session)
        for b in batches:
            session.refresh(b)
        return {"batches": [b.model_dump() for b in batches], "run": r.model_dump(), "routed": moved,
                "seeded_documents": seeded, "policyholders": holders}


@router.post("/reset", dependencies=[Depends(require("data:ingest"))])
def reset():
    engine.dispose()
    reset_db()
    with Session(engine) as session:
        seed_staff(session)
    return {"ok": True}


@router.get("/evaluation", dependencies=[Depends(require("quality:view"))])
def evaluation(session: Session = Depends(get_session)):
    return evaluate(session)


@router.get("/audit/verify", dependencies=[Depends(require("audit:view"))])
def audit_verify(session: Session = Depends(get_session)):
    return audit.verify_chain(session)


@router.get("/meta")
def meta(_: CurrentUser, session: Session = Depends(get_session)):
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
