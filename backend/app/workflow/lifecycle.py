"""Claim lifecycle: allowed transitions, who may make them, routing after analysis, and notifications.

States are shared by the staff app and the policyholder portal; `PORTAL_LABEL` is all a policyholder sees."""

from __future__ import annotations

from itertools import cycle

from fastapi import HTTPException
from sqlmodel import Session, select

from app import audit
from app.auth.permissions import has
from app.models import Claim, Finding, Notification, User

# (from, to) -> permission needed (None: system-only transition)
TRANSITIONS: dict[tuple[str, str], str | None] = {
    ("DRAFT", "SUBMITTED"): "claim:file_own",
    ("SUBMITTED", "TRIAGE"): None,
    ("SUBMITTED", "INVESTIGATION"): None,
    ("TRIAGE", "APPROVED"): "claim:approve_clean",
    ("TRIAGE", "INVESTIGATION"): "claim:triage",
    ("TRIAGE", "INFO_REQUESTED"): "evidence:request",
    ("INVESTIGATION", "INFO_REQUESTED"): "evidence:request",
    ("INFO_REQUESTED", "INVESTIGATION"): None,  # policyholder responded (system moves it back)
    ("INFO_REQUESTED", "TRIAGE"): None,
    ("INVESTIGATION", "ESCALATED_SENIOR"): "escalation:create",
    ("INVESTIGATION", "ESCALATED_SIU"): "escalation:create",
    ("ESCALATED_SENIOR", "INVESTIGATION"): "escalation:resolve",
    ("ESCALATED_SIU", "INVESTIGATION"): "escalation:resolve",
    ("INVESTIGATION", "PENDING_APPROVAL"): "claim:decide",
    ("PENDING_APPROVAL", "APPROVED"): "approval:resolve",
    ("PENDING_APPROVAL", "PARTIALLY_APPROVED"): "approval:resolve",
    ("PENDING_APPROVAL", "REJECTED"): "approval:resolve",
    ("PENDING_APPROVAL", "INVESTIGATION"): "approval:resolve",
    ("INVESTIGATION", "APPROVED"): "claim:decide",
    ("INVESTIGATION", "PARTIALLY_APPROVED"): "claim:decide",
    ("INVESTIGATION", "REJECTED"): "claim:decide",
    ("APPROVED", "SETTLED"): None,
    ("PARTIALLY_APPROVED", "SETTLED"): None,
    ("REJECTED", "RECONSIDERATION"): "claim:file_own",
    ("PARTIALLY_APPROVED", "RECONSIDERATION"): "claim:file_own",
    ("RECONSIDERATION", "INVESTIGATION"): None,
}
for _s in ("TRIAGE", "INVESTIGATION", "ESCALATED_SENIOR", "ESCALATED_SIU", "INFO_REQUESTED"):
    TRANSITIONS.setdefault((_s, "ON_HOLD"), "claim:hold")
TRANSITIONS[("ON_HOLD", "INVESTIGATION")] = "escalation:resolve"

PORTAL_LABEL = {
    "DRAFT": "Draft", "SUBMITTED": "Submitted", "TRIAGE": "Under review", "INVESTIGATION": "Under review",
    "ESCALATED_SENIOR": "Under review", "ESCALATED_SIU": "Under review", "ON_HOLD": "Under review",
    "PENDING_APPROVAL": "Under review", "INFO_REQUESTED": "Action needed", "APPROVED": "Decision made",
    "PARTIALLY_APPROVED": "Decision made", "REJECTED": "Decision made", "RECONSIDERATION": "Under review",
    "SETTLED": "Paid",
}
OPEN_STATES = {"SUBMITTED", "TRIAGE", "INVESTIGATION", "INFO_REQUESTED", "ESCALATED_SENIOR", "ESCALATED_SIU",
               "ON_HOLD", "PENDING_APPROVAL", "RECONSIDERATION"}


def notify(session: Session, message: str, *, kind: str, claim_id: str | None = None, username: str | None = None,
           role: str | None = None) -> None:
    session.add(Notification(username=username, role=role, claim_id=claim_id, kind=kind, message=message))


def transition(session: Session, claim: Claim, to: str, actor: User | str, reason: str | None = None,
               check: bool = True) -> None:
    key = (claim.state, to)
    if key not in TRANSITIONS:
        raise HTTPException(409, f"Claim {claim.claim_id} cannot move from {claim.state} to {to}")
    perm = TRANSITIONS[key]
    if check and isinstance(actor, User):
        if perm is None or not has(actor.role, perm):
            raise HTTPException(403, f"{actor.role} cannot move a claim from {claim.state} to {to}")
    before = claim.state
    claim.state = to
    session.add(claim)
    audit.record(session, action="STATE_CHANGED", entity="claim", entity_id=claim.claim_id, claim_id=claim.claim_id,
                 actor=actor, reason=reason, before={"state": before}, after={"state": to})
    holder = policyholder_of(session, claim)
    if holder and PORTAL_LABEL.get(before) != PORTAL_LABEL.get(to):
        notify(session, f"Claim {claim.claim_id}: {PORTAL_LABEL[to].lower()}", kind="status", claim_id=claim.claim_id,
               username=holder.username)


def policyholder_of(session: Session, claim: Claim) -> User | None:
    holders = session.info.get("holders")
    if holders is None:  # cached per session: routing touches every claim
        holders = {u.policy_no: u for u in session.exec(select(User).where(User.role == "POLICYHOLDER"))}
        session.info["holders"] = holders
    return holders.get(claim.policy_no)


def route_after_analysis(session: Session) -> dict:
    """SUBMITTED claims leave intake: clean ones to the handler's triage queue, flagged ones to an analyst."""
    analysts = [u.username for u in session.exec(select(User).where(User.role == "ANALYST", User.active)).all()]
    next_analyst = cycle(sorted(analysts)) if analysts else None
    flagged_ids = {f.claim_id for f in session.exec(select(Finding))}
    moved = {"TRIAGE": 0, "INVESTIGATION": 0}
    for c in session.exec(select(Claim).where(Claim.status == "PENDING", Claim.state == "SUBMITTED")):
        if c.claim_id in flagged_ids:
            transition(session, c, "INVESTIGATION", "system:router", "findings detected", check=False)
            if next_analyst:
                c.assigned_to = next(next_analyst)
                audit.record(session, action="ASSIGNED", entity="claim", entity_id=c.claim_id, claim_id=c.claim_id,
                             actor="system:router", after={"assigned_to": c.assigned_to})
                notify(session, f"New investigation {c.claim_id}", kind="assignment", claim_id=c.claim_id,
                       username=c.assigned_to)
            moved["INVESTIGATION"] += 1
        else:
            transition(session, c, "TRIAGE", "system:router", "no findings — fast-track review", check=False)
            moved["TRIAGE"] += 1
    return moved
