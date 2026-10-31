from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.ai.explainer import explain
from app.auth.deps import require
from app.db import get_session
from app.models import User
from app.engine.base import CATEGORY_LABELS
from app.models import (Claim, ClaimAssessment, ClaimDecision, Document, DocumentScan, Finding, FindingDecision,
                        InvoiceLine, Policy, Provider, Settlement)

router = APIRouter(prefix="/api", tags=["findings"])
VIEW = Depends(require("findings:view"))


class Filters(BaseModel):
    claim_type: str | None = None
    provider_id: str | None = None
    category: str | None = None
    severity: str | None = None
    status: str | None = None
    finding_type: str | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    date_from: date | None = None
    date_to: date | None = None
    q: str | None = None


def _effective(f: Finding) -> float:
    if f.status == "DISMISSED":
        return 0.0
    if f.status == "CORRECTED" and f.corrected_amount is not None:
        return f.corrected_amount
    return f.leakage_amount or 0.0


def _claim_ok(c: Claim, flt: Filters) -> bool:
    return ((not flt.claim_type or c.claim_type == flt.claim_type)
            and (not flt.provider_id or c.provider_id == flt.provider_id)
            and (not flt.date_from or c.incident_date >= flt.date_from)
            and (not flt.date_to or c.incident_date <= flt.date_to)
            and (not flt.q or flt.q.upper() in c.claim_id.upper()))


def _filtered(session: Session, flt: Filters) -> list[tuple[Finding, Claim]]:
    rows = session.exec(select(Finding, Claim).join(Claim, Claim.claim_id == Finding.claim_id)).all()
    out = []
    for f, c in rows:
        amt = f.leakage_amount or 0
        if not _claim_ok(c, flt):
            continue
        if flt.category and f.category != flt.category or flt.severity and f.severity != flt.severity:
            continue
        if flt.status and f.status != flt.status or flt.finding_type and f.finding_type != flt.finding_type:
            continue
        if flt.min_amount is not None and amt < flt.min_amount or flt.max_amount is not None and amt > flt.max_amount:
            continue
        out.append((f, c))
    return out


def _row(f: Finding, c: Claim, providers: dict) -> dict:
    p = providers.get(c.provider_id)
    return f.model_dump(exclude={"evidence", "ai_explanation"}) | {
        "category_label": CATEGORY_LABELS.get(f.category, f.category), "effective_amount": _effective(f),
        "claim_type": c.claim_type, "provider_id": c.provider_id, "provider_name": p.name if p else None,
        "incident_date": c.incident_date, "claimant_name": c.claimant_name,
        "requested_amount": c.requested_amount}


@router.get("/findings", dependencies=[VIEW])
def list_findings(flt: Filters = Depends(), sort: str = "-leakage_amount", limit: int = Query(500, le=5000),
                  session: Session = Depends(get_session)):
    providers = {p.provider_id: p for p in session.exec(select(Provider))}
    rows = [_row(f, c, providers) for f, c in _filtered(session, flt)]
    key, rev = sort.lstrip("-"), sort.startswith("-")
    present = sorted((r for r in rows if r.get(key) is not None),
                     key=lambda r: r[key] if isinstance(r[key], (int, float)) else str(r[key]), reverse=rev)
    rows = present + [r for r in rows if r.get(key) is None]  # blanks last either way
    return {"total": len(rows), "items": rows[:limit]}


@router.get("/dashboard/summary", dependencies=[VIEW])
def summary(flt: Filters = Depends(), session: Session = Depends(get_session)):
    claims = [c for c in session.exec(select(Claim).where(Claim.status == "PENDING")) if _claim_ok(c, flt)]
    pairs = _filtered(session, flt)
    providers = {p.provider_id: p for p in session.exec(select(Provider))}
    live = [(f, c) for f, c in pairs if f.status != "DISMISSED"]
    calc = sum(_effective(f) for f, _ in live if f.amount_basis == "CALCULATED")
    est = sum(_effective(f) for f, _ in live if f.amount_basis == "ESTIMATED")
    by_cat: dict[str, dict] = defaultdict(lambda: {"count": 0, "calculated": 0.0, "estimated": 0.0})
    by_sev: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    by_prov: dict[str, dict] = defaultdict(lambda: {"count": 0, "calculated": 0.0, "estimated": 0.0})
    for f, c in pairs:
        by_status[f.status] += 1
    for f, c in live:
        b = "calculated" if f.amount_basis == "CALCULATED" else "estimated"
        by_cat[f.category]["count"] += 1
        by_cat[f.category][b] += _effective(f) if f.amount_basis != "NOT_DETERMINABLE" else 0
        by_sev[f.severity] += 1
        by_type[f.finding_type] += 1
        by_prov[c.provider_id]["count"] += 1
        by_prov[c.provider_id][b] += _effective(f) if f.amount_basis != "NOT_DETERMINABLE" else 0
    flagged = {c.claim_id for f, c in live}
    billed = sum(c.requested_amount for c in claims)
    return {
        "claims_processed": len(claims), "claims_flagged": len(flagged),
        "flagged_pct": round(100 * len(flagged) / len(claims), 1) if claims else 0,
        "findings": len(live), "calculated_leakage": round(calc, 2), "estimated_leakage": round(est, 2),
        "needs_info": by_type.get("NEEDS_INFO", 0), "billed_amount": round(billed, 2),
        "proposed_amount": round(sum(c.proposed_settlement or 0 for c in claims), 2),
        "by_category": sorted(({"category": k, "label": CATEGORY_LABELS.get(k, k)} | v for k, v in by_cat.items()),
                              key=lambda r: -(r["calculated"] + r["estimated"])),
        "by_severity": by_sev, "by_status": by_status, "by_finding_type": by_type,
        "top_providers": sorted(({"provider_id": k, "name": providers[k].name if k in providers else k} | v
                                 for k, v in by_prov.items()),
                                key=lambda r: -(r["calculated"] + r["estimated"]))[:8],
    }


def _claim_bundle(session: Session, claim_id: str) -> dict:
    c = session.get(Claim, claim_id)
    if not c:
        raise HTTPException(404, f"Claim {claim_id} not found")
    from app.api.claims import review_status, suggested_amount

    lines = session.exec(select(InvoiceLine).where(InvoiceLine.claim_id == claim_id)
                         .order_by(col(InvoiceLine.line_id))).all()
    findings = session.exec(select(Finding).where(Finding.claim_id == claim_id)).all()
    decisions = session.exec(select(ClaimDecision).where(ClaimDecision.claim_id == claim_id)
                             .order_by(col(ClaimDecision.id))).all()
    assessment = session.get(ClaimAssessment, claim_id)
    status = "PAID" if c.status == "PAID" else review_status(findings, decisions[-1] if decisions else None)
    doc = session.exec(select(Document).where(Document.claim_id == claim_id)).first()
    scan = None if doc is None else session.exec(select(DocumentScan).where(DocumentScan.doc_id == doc.id)
                                                 .order_by(col(DocumentScan.id).desc())).first()
    return {"claim": c, "lines": lines, "policy": session.get(Policy, c.policy_no),
            "provider": session.get(Provider, c.provider_id),
            "settlements": session.exec(select(Settlement).where(Settlement.claim_id == claim_id)).all(),
            "assessment": assessment,
            "findings": [f.model_dump(exclude={"evidence"}) | {"category_label": CATEGORY_LABELS.get(f.category),
                                                                "effective_amount": _effective(f)}
                         for f in findings],
            "decisions": decisions, "review_status": status,
            "suggested_amount": suggested_amount(c, findings, assessment),
            "document": None if doc is None else {"id": doc.id, "filename": doc.filename,
                                                  "scan": None if scan is None else scan.model_dump()}}


@router.get("/claims/{claim_id}", dependencies=[Depends(require("claims:view"))])
def get_claim(claim_id: str, session: Session = Depends(get_session)):
    return _claim_bundle(session, claim_id)


@router.get("/findings/{finding_id}", dependencies=[VIEW])
def get_finding(finding_id: int, session: Session = Depends(get_session)):
    f = session.get(Finding, finding_id)
    if not f:
        raise HTTPException(404, "Finding not found")
    related = _claim_bundle(session, f.related_claim_id) if f.related_claim_id else None
    actions = session.exec(select(FindingDecision).where(FindingDecision.finding_id == finding_id)
                           .order_by(col(FindingDecision.id))).all()
    return {"finding": f.model_dump() | {"category_label": CATEGORY_LABELS.get(f.category),
                                         "effective_amount": _effective(f)},
            "claim": _claim_bundle(session, f.claim_id), "related": related, "actions": actions}


@router.post("/findings/{finding_id}/explain", dependencies=[VIEW])
def explain_finding(finding_id: int, refresh: bool = False, session: Session = Depends(get_session)):
    f = session.get(Finding, finding_id)
    if not f:
        raise HTTPException(404, "Finding not found")
    if f.ai_explanation and not refresh:
        return f.ai_explanation
    f.ai_explanation = explain(f, session.get(Claim, f.claim_id))
    session.add(f)
    session.commit()
    return f.ai_explanation
