"""Claims register: every submission with its derived review status, and claim-level decisions."""

from collections import Counter, defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.api.findings import Filters, _claim_ok, _effective
from app.auth.deps import require
from app.db import get_session
from app.models import Claim, ClaimAssessment, ClaimDecision, Document, DocumentScan, Finding, Provider, User

router = APIRouter(prefix="/api", tags=["claims"])

DECISIONS = ("APPROVED", "PARTIALLY_APPROVED", "ADJUSTED", "ON_HOLD", "REJECTED")
SEV_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


OPEN_FINDING = {"OPEN", "EVIDENCE_REQUESTED", "ESCALATED", "PENDING_APPROVAL"}


def review_status(findings: list[Finding], decision: ClaimDecision | None) -> str:
    if decision is not None and decision.status == "FINAL":
        return decision.decision
    if decision is not None and decision.status == "PENDING_APPROVAL":
        return "PENDING_APPROVAL"
    if not findings:
        return "CLEAN"
    open_ = [f for f in findings if f.status in OPEN_FINDING]
    if not open_:
        return "REVIEWED"
    return "NEEDS_INFO" if all(f.finding_type == "NEEDS_INFO" for f in open_) else "OPEN"


def suggested_amount(claim: Claim, findings: list[Finding], assessment: ClaimAssessment | None) -> float | None:
    """Proposed settlement less the confirmed / corrected / open calculated leakage (dismissed is paid)."""
    if claim.proposed_settlement is None:
        return assessment.expected_settlement if assessment else None
    cut = sum(_effective(f) for f in findings if f.amount_basis == "CALCULATED" and f.status != "DISMISSED")
    return round(max(claim.proposed_settlement - cut, 0), 2)


def _latest_decisions(session: Session) -> dict[str, ClaimDecision]:
    out: dict[str, ClaimDecision] = {}
    for d in session.exec(select(ClaimDecision).order_by(col(ClaimDecision.id))):
        out[d.claim_id] = d
    return out


def _integrity_by_claim(session: Session) -> dict[str, dict]:
    out = {}
    for doc in session.exec(select(Document).where(Document.claim_id.is_not(None))):
        s = session.exec(select(DocumentScan).where(DocumentScan.doc_id == doc.id)
                         .order_by(col(DocumentScan.id).desc())).first()
        out[doc.claim_id] = {"doc_id": doc.id, "filename": doc.filename,
                             "level": s.level if s else None, "score": s.score if s else None,
                             "status": s.status if s else None}
    return out


@router.get("/claims", dependencies=[Depends(require("claims:view"))])
def list_claims(flt: Filters = Depends(), review_status_: str | None = Query(None, alias="review_status"),
                source: str | None = None, state: str | None = None, assigned_to: str | None = None,
                ids: str | None = None, scope: str = "PENDING", sort: str = "-leakage",
                limit: int = Query(200, le=5000), offset: int = 0, session: Session = Depends(get_session)):
    q = select(Claim)
    if scope != "ALL":
        q = q.where(Claim.status == scope)
    wanted_ids = set(ids.split(",")) if ids else None
    claims = [c for c in session.exec(q) if _claim_ok(c, flt) and (not source or c.source == source)
              and (not state or c.state in state.split(",")) and (not assigned_to or c.assigned_to == assigned_to)
              and (wanted_ids is None or c.claim_id in wanted_ids)]
    findings: dict[str, list[Finding]] = defaultdict(list)
    for f in session.exec(select(Finding)):
        findings[f.claim_id].append(f)
    assessments = {a.claim_id: a for a in session.exec(select(ClaimAssessment))}
    decisions = _latest_decisions(session)
    integrity = _integrity_by_claim(session)
    providers = {p.provider_id: p.name for p in session.exec(select(Provider))}

    rows = []
    for c in claims:
        fs = findings.get(c.claim_id, [])
        live = [f for f in fs if f.status != "DISMISSED"]
        status = "PAID" if c.status == "PAID" else review_status(fs, decisions.get(c.claim_id))
        sev = max((f.severity for f in live), key=lambda s: SEV_RANK.get(s, 0), default=None)
        if flt.severity and sev != flt.severity:
            continue
        if flt.category and not any(f.category == flt.category for f in fs):
            continue
        a = assessments.get(c.claim_id)
        d = decisions.get(c.claim_id)
        rows.append({
            "claim_id": c.claim_id, "claimant_name": c.claimant_name, "provider_id": c.provider_id,
            "provider_name": providers.get(c.provider_id), "claim_type": c.claim_type,
            "incident_date": c.incident_date, "submitted_date": c.submitted_date, "source": c.source,
            "requested_amount": c.requested_amount, "proposed_settlement": c.proposed_settlement,
            "expected_settlement": a.expected_settlement if a else None,
            "leakage_calculated": round(sum(_effective(f) for f in live if f.amount_basis == "CALCULATED"), 2),
            "leakage_estimated": round(sum(_effective(f) for f in live if f.amount_basis == "ESTIMATED"), 2),
            "findings_confirmed": sum(f.finding_type == "CONFIRMED_VIOLATION" for f in live),
            "findings_suspected": sum(f.finding_type == "SUSPECTED_PATTERN" for f in live),
            "findings_needs_info": sum(f.finding_type == "NEEDS_INFO" for f in live),
            "findings_open": sum(f.status == "OPEN" for f in fs),
            "max_severity": sev, "review_status": status, "integrity": integrity.get(c.claim_id),
            "state": c.state, "assigned_to": c.assigned_to,
            "decision": None if d is None else {"decision": d.decision, "approved_amount": d.approved_amount,
                                                 "user": d.user, "created_at": d.created_at},
        })
    counts = Counter(r["review_status"] for r in rows)
    if review_status_:
        wanted = DECISIONS if review_status_ == "DECIDED" else (review_status_,)
        rows = [r for r in rows if r["review_status"] in wanted]

    key, rev = sort.lstrip("-"), sort.startswith("-")
    keyf = {"leakage": lambda r: r["leakage_calculated"] + r["leakage_estimated"]}.get(key, lambda r: r.get(key))
    present = sorted((r for r in rows if keyf(r) is not None), key=lambda r: (keyf(r) if not isinstance(keyf(r), str)
                                                                               else keyf(r).lower()), reverse=rev)
    rows = present + [r for r in rows if keyf(r) is None]
    return {"total": len(rows), "counts": dict(counts), "items": rows[offset:offset + limit]}
