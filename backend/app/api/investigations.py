"""Investigation workspace API: the claim with all its evidence, per-finding decisions, evidence requests,
escalations, notes, maker-checker approvals, claim decisions within authority, and the investigation report.
Every write goes through the lifecycle (`workflow/lifecycle.py`) and the audit chain (`audit.py`)."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app import audit
from app.api.findings import _claim_bundle, _effective
from app.auth.deps import CurrentUser, require
from app.auth.permissions import AUTHORITY, OVERRIDE_REVIEW_THRESHOLD, has
from app.db import get_session
from app.engine.base import CATEGORY_LABELS
from app.models import (AuditEvent, Claim, ClaimDecision, ClaimLink, Document, Escalation, EvidenceRequest,
                        EvidenceResponse, ExtractedField, Finding, FindingDecision, Note, PolicyRule, Recommendation,
                        User)
from app.workflow.lifecycle import OPEN_STATES, PORTAL_LABEL, notify, policyholder_of, transition

router = APIRouter(prefix="/api", tags=["investigations"])

DECIDED = {"CONFIRMED", "CORRECTED", "DISMISSED"}
OPEN_FINDING = {"OPEN", "EVIDENCE_REQUESTED", "ESCALATED", "PENDING_APPROVAL"}
WORKING_STATES = {"INVESTIGATION", "TRIAGE"}


def _claim(session: Session, claim_id: str) -> Claim:
    c = session.get(Claim, claim_id)
    if not c:
        raise HTTPException(404, f"Claim {claim_id} not found")
    return c


def _assert_can_work(user: User, claim: Claim) -> None:
    if claim.state not in WORKING_STATES:
        raise HTTPException(409, f"Claim is {claim.state}; decisions are made while it is under investigation")
    if user.role == "ANALYST" and claim.assigned_to and claim.assigned_to != user.username:
        raise HTTPException(403, f"Claim is assigned to {claim.assigned_to}")


def corrected_settlement(claim: Claim, findings: list[Finding]) -> float | None:
    """Proposed settlement less every confirmed / corrected calculated finding (dismissed ones are paid)."""
    if claim.proposed_settlement is None:
        return None
    cut = sum(_effective(f) for f in findings if f.amount_basis == "CALCULATED" and f.status in ("CONFIRMED", "CORRECTED"))
    return round(max(claim.proposed_settlement - cut, 0), 2)


# ---------------------------------------------------------------- workspace bundle
@router.get("/investigations/{claim_id}")
def workspace(claim_id: str, user: User = Depends(require("claims:view")), session: Session = Depends(get_session)):
    c = _claim(session, claim_id)
    bundle = _claim_bundle(session, claim_id)
    findings = session.exec(select(Finding).where(Finding.claim_id == claim_id)).all()
    recs = session.exec(select(Recommendation).where(Recommendation.claim_id == claim_id)
                        .order_by(col(Recommendation.id))).all()
    decisions = session.exec(select(FindingDecision).where(FindingDecision.claim_id == claim_id)
                             .order_by(col(FindingDecision.id))).all()
    dec_by_f: dict[int, list] = defaultdict(list)
    for d in decisions:
        dec_by_f[d.finding_id].append(d.model_dump())
    rec_by_f: dict[int, list] = defaultdict(list)
    for r in recs:
        rec_by_f[r.finding_id].append(r.model_dump())
    bundle["findings"] = [f.model_dump() | {"category_label": CATEGORY_LABELS.get(f.category),
                                            "effective_amount": _effective(f),
                                            "recommendations": rec_by_f.get(f.id, []),
                                            "decisions": dec_by_f.get(f.id, [])} for f in findings]

    links = session.exec(select(ClaimLink).where((ClaimLink.claim_a == claim_id) | (ClaimLink.claim_b == claim_id))).all()
    related: dict[str, dict] = {}
    for l in links:
        other = l.claim_b if l.claim_a == claim_id else l.claim_a
        oc = session.get(Claim, other)
        r = related.setdefault(other, {"claim_id": other, "policy_no": oc.policy_no if oc else None,
                                       "claimant_name": oc.claimant_name if oc else None,
                                       "provider_id": oc.provider_id if oc else None, "status": oc.status if oc else None,
                                       "requested_amount": oc.requested_amount if oc else None, "links": []})
        r["links"].append({"rule": l.rule, "strength": l.strength, "attribute": l.attribute, "value": l.value,
                           "explanation": l.explanation})
    docs = session.exec(select(Document).where(Document.claim_id == claim_id)).all()
    fields = session.exec(select(ExtractedField).where(ExtractedField.claim_id == claim_id)).all()
    notes = [n for n in session.exec(select(Note).where(Note.claim_id == claim_id).order_by(col(Note.id)))
             if n.visibility != "SIU" or has(user.role, "siu:view")]
    rules = {r.rule_id: r.description for r in session.exec(select(PolicyRule))}
    events = session.exec(select(AuditEvent).where(AuditEvent.claim_id == claim_id).order_by(col(AuditEvent.id))).all()
    can_work = c.state in WORKING_STATES and not (user.role == "ANALYST" and c.assigned_to not in (None, user.username))
    return bundle | {
        "state": c.state, "portal_label": PORTAL_LABEL.get(c.state), "assigned_to": c.assigned_to,
        "related": sorted(related.values(), key=lambda r: -sum(l["strength"] == "STRONG" for l in r["links"])),
        "documents": [d.model_dump(exclude={"extraction"}) | {"fields": [f.model_dump() for f in fields if f.doc_id == d.id]}
                      for d in docs],
        "evidence_requests": [r.model_dump() | {"responses": [x.model_dump() for x in session.exec(
            select(EvidenceResponse).where(EvidenceResponse.request_id == r.id))]}
            for r in session.exec(select(EvidenceRequest).where(EvidenceRequest.claim_id == claim_id))],
        "escalations": session.exec(select(Escalation).where(Escalation.claim_id == claim_id)).all(),
        "notes": notes,
        "rule_text": rules,
        "timeline": [e.model_dump(exclude={"prev_hash"}) for e in events],
        "corrected_settlement": corrected_settlement(c, findings),
        "open_findings": sum(f.status in OPEN_FINDING for f in findings),
        "can": {"decide_findings": can_work and has(user.role, "finding:decide"),
                "decide_claim": can_work and has(user.role, "claim:decide"),
                "approve_clean": c.state == "TRIAGE" and has(user.role, "claim:approve_clean"),
                "request_evidence": c.state in WORKING_STATES and has(user.role, "evidence:request"),
                "escalate": can_work and has(user.role, "escalation:create"),
                "resolve_escalation": has(user.role, "escalation:resolve"),
                "approve": has(user.role, "approval:resolve"),
                "note": has(user.role, "note:write"), "siu_notes": has(user.role, "siu:view"),
                "hold": has(user.role, "claim:hold") and c.state in OPEN_STATES,
                "export": has(user.role, "report:export"), "authority": AUTHORITY.get(user.role)},
    }


# ---------------------------------------------------------------- per-finding decisions
class FindingDecisionBody(BaseModel):
    decision: str
    reason: str | None = None
    override_impact: float | None = None  # ACCEPT_CORRECTION with a different figure
    target: str | None = None  # ESCALATE: SENIOR | SIU
    message: str | None = None  # REQUEST_EVIDENCE
    doc_types: list[str] = []
    due_days: int = 7


def _request_evidence(session: Session, user: User, claim: Claim, finding_id: int | None, message: str,
                      doc_types: list[str], due_days: int) -> EvidenceRequest:
    req = EvidenceRequest(claim_id=claim.claim_id, finding_id=finding_id, message=message, doc_types=doc_types,
                          due_date=date.today() + timedelta(days=due_days), requested_by=user.username)
    session.add(req)
    session.flush()
    audit.record(session, action="EVIDENCE_REQUESTED", entity="evidence_request", entity_id=req.id,
                 claim_id=claim.claim_id, actor=user, after=req.model_dump(exclude={"id", "created_at"}))
    if claim.state != "INFO_REQUESTED":
        transition(session, claim, "INFO_REQUESTED", user, message)
    holder = policyholder_of(session, claim)
    if holder:
        notify(session, f"Action needed on claim {claim.claim_id}: {message}", kind="evidence_request",
               claim_id=claim.claim_id, username=holder.username)
    return req


@router.post("/findings/{finding_id}/decision")
def decide_finding(finding_id: int, body: FindingDecisionBody, session: Session = Depends(get_session),
                   user: User = Depends(require("finding:decide"))):
    f = session.get(Finding, finding_id) or _404("Finding")
    claim = _claim(session, f.claim_id)
    _assert_can_work(user, claim)
    d = body.decision.upper()
    before = {"status": f.status, "corrected_amount": f.corrected_amount}
    rec = session.exec(select(Recommendation).where(Recommendation.finding_id == f.id,
                                                    Recommendation.superseded == False)).first()  # noqa: E712
    fd = FindingDecision(finding_id=f.id, claim_id=claim.claim_id, decision=d, reason=(body.reason or "").strip() or None,
                         recommendation_id=rec.id if rec else None,
                         recommended_impact=rec.settlement_impact if rec else f.leakage_amount,
                         user=user.username, role=user.role)
    if d == "CONFIRM_DISCREPANCY":
        f.status = "CONFIRMED"
    elif d == "FALSE_POSITIVE":
        if not fd.reason:
            raise HTTPException(422, "Marking a false positive needs a reason")
        f.status = "DISMISSED"
    elif d == "REQUEST_EVIDENCE":
        if not (body.message or "").strip():
            raise HTTPException(422, "Say what evidence is needed")
        f.status = "EVIDENCE_REQUESTED"
        _request_evidence(session, user, claim, f.id, body.message.strip(), body.doc_types, body.due_days)
    elif d == "ESCALATE":
        target = (body.target or "").upper()
        if target not in ("SENIOR", "SIU") or not fd.reason:
            raise HTTPException(422, "Escalation needs a target (SENIOR or SIU) and a reason")
        esc = Escalation(claim_id=claim.claim_id, finding_id=f.id, target=target, reason=fd.reason, raised_by=user.username)
        session.add(esc)
        session.flush()
        audit.record(session, action="ESCALATED", entity="escalation", entity_id=esc.id, claim_id=claim.claim_id,
                     actor=user, reason=fd.reason, after={"target": target, "finding_id": f.id})
        transition(session, claim, f"ESCALATED_{target}", user, fd.reason)
        notify(session, f"{claim.claim_id} escalated to {target}: {fd.reason}", kind="escalation",
               claim_id=claim.claim_id, role=target)
        f.status = "ESCALATED"
    elif d == "ACCEPT_CORRECTION":
        if rec is None:
            raise HTTPException(409, "This finding has no recommended correction to accept")
        if body.override_impact is None:
            f.status, f.corrected_amount = "CONFIRMED", None
        else:
            if body.override_impact < 0 or not fd.reason:
                raise HTTPException(422, "An override needs a non-negative amount and a reason")
            fd.override_impact = round(body.override_impact, 2)
            gap = abs((rec.settlement_impact or 0) - fd.override_impact)
            if user.role == "ANALYST" and gap > OVERRIDE_REVIEW_THRESHOLD:
                fd.status, f.status = "PENDING_APPROVAL", "PENDING_APPROVAL"
                notify(session, f"Override on {claim.claim_id} needs approval (₹{gap:,.0f} from the recommendation)",
                       kind="approval", claim_id=claim.claim_id, role="SENIOR")
            else:
                f.status, f.corrected_amount = "CORRECTED", fd.override_impact
    else:
        raise HTTPException(422, f"Unknown decision {body.decision}")
    session.add(fd)
    session.add(f)
    session.flush()
    audit.record(session, action=f"FINDING_{d}", entity="finding", entity_id=f.id, claim_id=claim.claim_id, actor=user,
                 reason=fd.reason, before=before,
                 after={"status": f.status, "corrected_amount": f.corrected_amount, "decision_id": fd.id,
                        "recommendation_id": fd.recommendation_id, "recommended_impact": fd.recommended_impact,
                        "override_impact": fd.override_impact})
    session.commit()
    return workspace(claim.claim_id, user, session)


class ApprovalBody(BaseModel):
    approve: bool
    reason: str


@router.post("/finding-decisions/{decision_id}/approval")
def approve_override(decision_id: int, body: ApprovalBody, session: Session = Depends(get_session),
                     user: User = Depends(require("approval:resolve"))):
    fd = session.get(FindingDecision, decision_id) or _404("Decision")
    if fd.status != "PENDING_APPROVAL":
        raise HTTPException(409, "Nothing to approve")
    if fd.user == user.username:
        raise HTTPException(403, "The maker of an override cannot approve it")
    if not body.reason.strip():
        raise HTTPException(422, "A reason is required")
    f = session.get(Finding, fd.finding_id)
    fd.status, fd.approved_by, fd.approval_reason = ("APPROVED" if body.approve else "DECLINED"), user.username, body.reason
    if body.approve:
        f.status, f.corrected_amount = "CORRECTED", fd.override_impact
    else:
        f.status = "OPEN"  # back to the analyst; the recommendation still stands
    session.add_all([fd, f])
    audit.record(session, action="OVERRIDE_APPROVED" if body.approve else "OVERRIDE_DECLINED", entity="finding",
                 entity_id=f.id, claim_id=f.claim_id, actor=user, reason=body.reason,
                 after={"decision_id": fd.id, "override_impact": fd.override_impact, "finding_status": f.status})
    notify(session, f"Override on {f.claim_id} {'approved' if body.approve else 'declined'}", kind="approval",
           claim_id=f.claim_id, username=fd.user)
    session.commit()
    return workspace(f.claim_id, user, session)


# ---------------------------------------------------------------- evidence requests (claim level), escalations
class EvidenceBody(BaseModel):
    message: str
    doc_types: list[str] = []
    due_days: int = 7


@router.post("/claims/{claim_id}/evidence-requests")
def request_evidence(claim_id: str, body: EvidenceBody, session: Session = Depends(get_session),
                     user: User = Depends(require("evidence:request"))):
    claim = _claim(session, claim_id)
    if claim.state not in WORKING_STATES:
        raise HTTPException(409, f"Claim is {claim.state}")
    if not body.message.strip():
        raise HTTPException(422, "Say what evidence is needed")
    _request_evidence(session, user, claim, None, body.message.strip(), body.doc_types, body.due_days)
    session.commit()
    return workspace(claim_id, user, session)


class ResolveBody(BaseModel):
    resolution: str
    outcome: str | None = None  # SIU: SUBSTANTIATED | NOT_SUBSTANTIATED


@router.post("/escalations/{esc_id}/resolve")
def resolve_escalation(esc_id: int, body: ResolveBody, session: Session = Depends(get_session),
                       user: User = Depends(require("escalation:resolve"))):
    esc = session.get(Escalation, esc_id) or _404("Escalation")
    if esc.status != "OPEN":
        raise HTTPException(409, "Escalation already resolved")
    if esc.raised_by == user.username:
        raise HTTPException(403, "An escalation cannot be resolved by the person who raised it")
    if esc.target == "SIU" and user.role != "SIU":
        raise HTTPException(403, "SIU referrals are resolved by the SIU")
    if esc.target == "SENIOR" and user.role != "SENIOR":
        raise HTTPException(403, "Senior escalations are resolved by a senior reviewer")
    if not body.resolution.strip():
        raise HTTPException(422, "A resolution is required")
    if esc.target == "SIU" and body.outcome not in ("SUBSTANTIATED", "NOT_SUBSTANTIATED"):
        raise HTTPException(422, "SIU outcome must be SUBSTANTIATED or NOT_SUBSTANTIATED")
    esc.status, esc.resolution, esc.outcome = "RESOLVED", body.resolution.strip(), body.outcome
    esc.resolved_by, esc.resolved_at = user.username, datetime.now(UTC)
    session.add(esc)
    claim = _claim(session, esc.claim_id)
    audit.record(session, action="ESCALATION_RESOLVED", entity="escalation", entity_id=esc.id, claim_id=claim.claim_id,
                 actor=user, reason=esc.resolution, after={"outcome": esc.outcome, "target": esc.target})
    if esc.finding_id:
        f = session.get(Finding, esc.finding_id)
        if f and f.status == "ESCALATED":
            f.status = "OPEN"  # the assigned analyst records the final decision with the outcome in hand
            session.add(f)
    if claim.state.startswith("ESCALATED") and not session.exec(
            select(Escalation).where(Escalation.claim_id == claim.claim_id, Escalation.status == "OPEN",
                                     Escalation.id != esc.id)).first():
        transition(session, claim, "INVESTIGATION", user, f"{esc.target} resolved: {esc.resolution}")
    if claim.assigned_to:
        notify(session, f"{esc.target} resolved escalation on {claim.claim_id}"
                        f"{f' ({esc.outcome.lower().replace(chr(95), chr(32))})' if esc.outcome else ''}",
               kind="escalation", claim_id=claim.claim_id, username=claim.assigned_to)
    session.commit()
    return workspace(claim.claim_id, user, session)


# ---------------------------------------------------------------- notes
class NoteBody(BaseModel):
    text: str
    visibility: str = "INTERNAL"


@router.post("/claims/{claim_id}/notes")
def add_note(claim_id: str, body: NoteBody, session: Session = Depends(get_session),
             user: User = Depends(require("note:write"))):
    _claim(session, claim_id)
    vis = body.visibility.upper()
    if vis == "SIU" and not has(user.role, "siu:view"):
        raise HTTPException(403, "Only SIU and senior reviewers can write SIU notes")
    if not body.text.strip():
        raise HTTPException(422, "Empty note")
    n = Note(claim_id=claim_id, author=user.username, role=user.role, visibility=vis, text=body.text.strip())
    session.add(n)
    session.flush()
    audit.record(session, action="NOTE_ADDED", entity="note", entity_id=n.id, claim_id=claim_id, actor=user,
                 after={"visibility": vis, "text": n.text})
    session.commit()
    return workspace(claim_id, user, session)


# ---------------------------------------------------------------- claim decision (authority + maker-checker)
class ClaimDecisionBody(BaseModel):
    decision: str  # APPROVED | PARTIALLY_APPROVED | REJECTED | ON_HOLD
    reason: str
    approved_amount: float | None = None


@router.post("/investigations/{claim_id}/decision")
def decide_claim(claim_id: str, body: ClaimDecisionBody, user: CurrentUser,
                 session: Session = Depends(get_session)):
    claim = _claim(session, claim_id)
    d = body.decision.upper()
    if not body.reason.strip():
        raise HTTPException(422, "A reason is required")
    findings = session.exec(select(Finding).where(Finding.claim_id == claim_id)).all()
    if d == "ON_HOLD":
        if not has(user.role, "claim:hold"):
            raise HTTPException(403, "Only SIU can hold a claim")
        transition(session, claim, "ON_HOLD", user, body.reason)
        session.add(ClaimDecision(claim_id=claim_id, decision=d, reason=body.reason, user=user.username, role=user.role))
        session.commit()
        return workspace(claim_id, user, session)
    if d not in ("APPROVED", "PARTIALLY_APPROVED", "REJECTED"):
        raise HTTPException(422, "Decision must be APPROVED, PARTIALLY_APPROVED, REJECTED or ON_HOLD")
    fast_track = claim.state == "TRIAGE" and has(user.role, "claim:approve_clean")
    if not fast_track:
        if not has(user.role, "claim:decide"):
            raise HTTPException(403, f"{user.role} cannot decide claims")
        _assert_can_work(user, claim)
    open_ = [f for f in findings if f.status in OPEN_FINDING]
    if open_:
        raise HTTPException(409, f"{len(open_)} finding(s) still need a decision")
    suggested = corrected_settlement(claim, findings)
    amount = 0.0 if d == "REJECTED" else (body.approved_amount if body.approved_amount is not None else suggested)
    if amount is None or amount < 0:
        raise HTTPException(422, "An approved amount is required")
    if fast_track and (findings or amount > (AUTHORITY.get("HANDLER") or 0) or d != "APPROVED"):
        raise HTTPException(403, "Fast-track approval is only for clean claims within ₹50,000; route to an analyst")
    limit = AUTHORITY.get(user.role)
    needs_approval = d != "REJECTED" and limit is not None and amount > limit
    target = "PENDING_APPROVAL" if needs_approval else d
    cd = ClaimDecision(claim_id=claim_id, decision=d, approved_amount=round(amount, 2), reason=body.reason.strip(),
                       user=user.username, role=user.role, status="PENDING_APPROVAL" if needs_approval else "FINAL")
    session.add(cd)
    session.flush()
    audit.record(session, action="CLAIM_DECISION_PROPOSED" if needs_approval else "CLAIM_DECIDED", entity="claim",
                 entity_id=claim_id, claim_id=claim_id, actor=user, reason=cd.reason,
                 after={"decision": d, "approved_amount": cd.approved_amount, "suggested_amount": suggested,
                        "proposed_settlement": claim.proposed_settlement, "authority": limit})
    transition(session, claim, target, user, cd.reason, check=not fast_track)
    if needs_approval:
        notify(session, f"{claim_id}: {d.lower()} at ₹{amount:,.0f} exceeds {user.role.lower()} authority",
               kind="approval", claim_id=claim_id, role="SENIOR")
    session.commit()
    return workspace(claim_id, user, session)


@router.post("/investigations/{claim_id}/approval")
def approve_claim(claim_id: str, body: ApprovalBody, session: Session = Depends(get_session),
                  user: User = Depends(require("approval:resolve"))):
    claim = _claim(session, claim_id)
    cd = session.exec(select(ClaimDecision).where(ClaimDecision.claim_id == claim_id,
                                                  ClaimDecision.status == "PENDING_APPROVAL")
                      .order_by(col(ClaimDecision.id).desc())).first()
    if cd is None or claim.state != "PENDING_APPROVAL":
        raise HTTPException(409, "No decision is waiting for approval")
    if cd.user == user.username:
        raise HTTPException(403, "The maker of a decision cannot approve it")
    cd.status, cd.approved_by, cd.approval_reason = ("FINAL" if body.approve else "DECLINED"), user.username, body.reason
    session.add(cd)
    audit.record(session, action="CLAIM_DECISION_APPROVED" if body.approve else "CLAIM_DECISION_DECLINED",
                 entity="claim", entity_id=claim_id, claim_id=claim_id, actor=user, reason=body.reason,
                 after={"decision": cd.decision, "approved_amount": cd.approved_amount})
    transition(session, claim, cd.decision if body.approve else "INVESTIGATION", user, body.reason)
    session.commit()
    return workspace(claim_id, user, session)


# ---------------------------------------------------------------- queues
@router.get("/queues/mine")
def my_queue(user: User = Depends(require("claims:view")), session: Session = Depends(get_session)):
    q = select(Claim).where(Claim.status == "PENDING")
    if user.role == "HANDLER":
        q = q.where(col(Claim.state).in_(["TRIAGE", "SUBMITTED"]))
    elif user.role == "ANALYST":
        q = q.where(Claim.assigned_to == user.username, col(Claim.state).in_(
            ["INVESTIGATION", "INFO_REQUESTED", "ESCALATED_SENIOR", "ESCALATED_SIU", "PENDING_APPROVAL", "RECONSIDERATION"]))
    elif user.role == "SENIOR":
        q = q.where(col(Claim.state).in_(["ESCALATED_SENIOR", "PENDING_APPROVAL"]) |
                    col(Claim.claim_id).in_(select(FindingDecision.claim_id).where(FindingDecision.status == "PENDING_APPROVAL")))
    elif user.role == "SIU":
        q = q.where(col(Claim.state).in_(["ESCALATED_SIU", "ON_HOLD"]))
    claims = session.exec(q).all()
    return {"role": user.role, "claim_ids": sorted(c.claim_id for c in claims)}


def _404(what: str):
    raise HTTPException(404, f"{what} not found")
