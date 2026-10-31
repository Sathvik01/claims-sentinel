"""Policyholder portal: file a claim, upload evidence, track progress, answer requests, read decisions.

Every response is built explicitly here — findings, scores, integrity results, related claims, internal notes and
SIU activity never reach a policyholder. Queries are always scoped to the signed-in policyholder's policy."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlmodel import Session, col, select

from app import audit
from app.api.documents import MIME, attach_llm_evidence
from app.auth.deps import own_claim_or_403, require
from app.config import settings
from app.db import get_session
from app.documents.extractor import extract
from app.engine.runner import run_analysis
from app.forensics.agent import run_scan
from app.ingestion.service import ingest_records
from app.models import (AuditEvent, Claim, ClaimDecision, Document, DocumentScan, EvidenceRequest, EvidenceResponse,
                        Finding, IngestionError, InvoiceLine, Member, Policy, Provider, Recommendation, Settlement, User)
from app.workflow.lifecycle import PORTAL_LABEL, notify, route_after_analysis, transition

router = APIRouter(prefix="/api/portal", tags=["portal"])
PH = Depends(require("portal:access"))

NEXT_STEP = {
    "DRAFT": "Add your bills and submit the claim.",
    "SUBMITTED": "We are checking your documents.",
    "TRIAGE": "A claims handler is reviewing your claim.",
    "INVESTIGATION": "A claims analyst is reviewing your bills against your policy.",
    "ESCALATED_SENIOR": "Your claim is with a senior reviewer.",
    "ESCALATED_SIU": "Your claim is under additional review.",
    "ON_HOLD": "Your claim is under additional review.",
    "PENDING_APPROVAL": "The decision is awaiting final approval.",
    "INFO_REQUESTED": "We need more information from you — see the request below.",
    "APPROVED": "Payment will be sent to your registered account.",
    "PARTIALLY_APPROVED": "Payment of the approved amount will be sent to your registered account.",
    "REJECTED": "You can ask us to reconsider with a reason.",
    "RECONSIDERATION": "We are reconsidering the decision.",
    "SETTLED": "Paid.",
}


def _own(session: Session, user: User, claim_id: str) -> Claim:
    c = session.get(Claim, claim_id)
    if not c:
        raise HTTPException(404, "Claim not found")
    own_claim_or_403(user, c)
    return c


def _decision(session: Session, claim_id: str) -> ClaimDecision | None:
    return session.exec(select(ClaimDecision).where(ClaimDecision.claim_id == claim_id, ClaimDecision.status == "FINAL")
                        .order_by(col(ClaimDecision.id).desc())).first()


def _summary(session: Session, c: Claim) -> dict:
    d = _decision(session, c.claim_id)
    open_req = session.exec(select(EvidenceRequest).where(EvidenceRequest.claim_id == c.claim_id,
                                                          EvidenceRequest.status == "OPEN")).all()
    prov = session.get(Provider, c.provider_id)
    return {"claim_id": c.claim_id, "member_id": c.member_id, "patient": c.claimant_name, "claim_type": c.claim_type,
            "hospital": prov.name if prov else c.provider_id, "incident_date": c.incident_date,
            "submitted_date": c.submitted_date, "amount_claimed": c.requested_amount,
            "status": PORTAL_LABEL.get(c.state, "Under review"), "next_step": NEXT_STEP.get(c.state, ""),
            "action_needed": len(open_req), "approved_amount": d.approved_amount if d else None}


@router.get("/me", dependencies=[PH])
def home(user: User = PH, session: Session = Depends(get_session)):
    pol = session.get(Policy, user.policy_no)
    paid = sum(s.paid_amount for c in session.exec(select(Claim).where(Claim.policy_no == user.policy_no, Claim.status == "PAID"))
               for s in session.exec(select(Settlement).where(Settlement.claim_id == c.claim_id))
               if pol and pol.start_date <= c.incident_date <= pol.end_date)
    members = session.exec(select(Member).where(Member.policy_no == user.policy_no)).all()
    claims = session.exec(select(Claim).where(Claim.policy_no == user.policy_no)
                          .order_by(col(Claim.submitted_date).desc())).all()
    return {
        "name": user.full_name,
        "policy": None if pol is None else {"policy_no": pol.policy_no, "plan": pol.plan, "status": pol.status,
                                            "start_date": pol.start_date, "end_date": pol.end_date,
                                            "sum_insured": pol.sum_insured, "used": round(paid, 2),
                                            "remaining": round(max(pol.sum_insured - paid, 0), 2),
                                            "deductible": pol.deductible, "copay_pct": pol.copay_pct,
                                            "room_rent_limit": pol.room_rent_limit},
        "members": [{"member_id": m.member_id, "name": m.name, "relationship": m.relationship} for m in members],
        "claims": [_summary(session, c) for c in claims],
    }


@router.get("/providers", dependencies=[PH])
def providers(session: Session = Depends(get_session)):
    return [{"provider_id": p.provider_id, "name": p.name, "city": p.city, "type": p.provider_type}
            for p in session.exec(select(Provider).where(Provider.network).order_by(col(Provider.name)))]


def _timeline(session: Session, c: Claim) -> list[dict]:
    out, last = [], None
    for e in session.exec(select(AuditEvent).where(AuditEvent.claim_id == c.claim_id,
                                                   col(AuditEvent.action).in_(["CLAIM_STARTED", "STATE_CHANGED"]))
                          .order_by(col(AuditEvent.id))):
        lab = "Draft" if e.action == "CLAIM_STARTED" else PORTAL_LABEL.get((e.after or {}).get("state", ""), "Under review")
        if lab != last:
            out.append({"at": e.at, "status": lab})
            last = lab
    return out


def _plain(r: Recommendation, lines: dict[str, InvoiceLine]) -> str:
    item = lines[r.target_line_ids[0]].description if r.target_line_ids and r.target_line_ids[0] in lines else "This item"
    clause = f" ({r.clause_ref})" if r.clause_ref else ""
    return {
        "REMOVE_LINE": f"{item} was billed twice; the repeated charge is not payable.",
        "REMOVE_EXCLUDED_ITEM": f"{item} is a non-payable item under your policy{clause}.",
        "CAP_DAILY_RATE": f"{item} is covered up to the daily limit in your policy{clause}.",
        "CAP_AT_SUBLIMIT": f"{item} is covered up to its sub-limit{clause}.",
        "LIMIT_SESSIONS": f"Sessions beyond the yearly limit are not covered{clause}.",
        "CORRECT_ARITHMETIC": f"{item} is payable at quantity × rate as itemised on the bill.",
        "ALIGN_TO_DOCUMENT": f"{item} is payable as shown on the hospital's own bill and discharge summary.",
        "APPLY_DEDUCTIBLE": f"Your policy's deductible applies to this claim{clause}.",
        "APPLY_COPAY": f"Your policy's co-payment applies to this claim{clause}.",
        "APPLY_SI_CAP": "Payments in a policy year cannot exceed your sum insured.",
        "REJECT_CLAIM": (r.clause_text or "This claim is not payable under your policy") + clause + ".",
    }.get(r.type, r.clause_text or "Adjusted under your policy terms.")


def _letter(session: Session, c: Claim) -> dict | None:
    d = _decision(session, c.claim_id)
    if d is None or d.decision not in ("APPROVED", "PARTIALLY_APPROVED", "REJECTED"):
        return None
    applied = {f.id for f in session.exec(select(Finding).where(Finding.claim_id == c.claim_id,
                                                                 col(Finding.status).in_(["CONFIRMED", "CORRECTED"])))}
    lines = {l.line_id: l for l in session.exec(select(InvoiceLine).where(InvoiceLine.claim_id == c.claim_id))}
    recs = [r for r in session.exec(select(Recommendation).where(Recommendation.claim_id == c.claim_id,
                                                                 Recommendation.superseded == False))  # noqa: E712
            if r.finding_id in applied]
    return {"decision": {"APPROVED": "Approved", "PARTIALLY_APPROVED": "Approved with adjustments",
                         "REJECTED": "Not approved"}[d.decision],
            "decided_on": d.created_at, "amount_claimed": c.requested_amount, "approved_amount": d.approved_amount,
            "adjustments": [{"explanation": _plain(r, lines), "amount_not_payable": r.delta if r.type not in (
                "APPLY_DEDUCTIBLE", "APPLY_COPAY", "APPLY_SI_CAP") else r.settlement_impact} for r in recs],
            "paid": c.state == "SETTLED", "payout_account": c.payout_account,
            "can_request_reconsideration": c.state in ("REJECTED", "PARTIALLY_APPROVED")}


@router.get("/claims/{claim_id}", dependencies=[PH])
def claim_view(claim_id: str, user: User = PH, session: Session = Depends(get_session)):
    c = _own(session, user, claim_id)
    docs = session.exec(select(Document).where(Document.claim_id == claim_id)).all()
    reqs = session.exec(select(EvidenceRequest).where(EvidenceRequest.claim_id == claim_id)).all()
    lines = session.exec(select(InvoiceLine).where(InvoiceLine.claim_id == claim_id).order_by(col(InvoiceLine.line_id))).all()
    return _summary(session, c) | {
        "state": c.state, "timeline": _timeline(session, c), "admission_date": c.admission_date,
        "discharge_date": c.discharge_date, "invoice_no": c.invoice_no,
        "lines": [{"description": l.description, "date": l.service_date, "quantity": l.quantity, "amount": l.amount}
                  for l in lines],
        "documents": [{"id": d.id, "filename": d.filename, "type": d.doc_type, "uploaded": d.created_at,
                       "extracted": d.extraction} for d in docs],
        "requests": [{"id": r.id, "message": r.message, "doc_types": r.doc_types, "due_date": r.due_date,
                      "status": r.status, "asked_on": r.created_at} for r in reqs],
        "letter": _letter(session, c),
    }


# ---------------------------------------------------------------- file a claim
class DraftBody(BaseModel):
    member_id: str
    claim_type: str
    provider_id: str
    admission_date: date
    discharge_date: date | None = None
    description: str | None = None
    payout_account: str | None = None


@router.post("/claims", dependencies=[Depends(require("claim:file_own"))])
def start_claim(body: DraftBody, user: User = PH, session: Session = Depends(get_session)):
    m = session.get(Member, body.member_id)
    if not m or m.policy_no != user.policy_no:
        raise HTTPException(422, "Choose a member covered by your policy")
    n = len(session.exec(select(Claim.claim_id).where(col(Claim.claim_id).startswith("CLM-P"))).all()) + 1
    row = dict(claim_id=f"CLM-P{n:05d}", policy_no=user.policy_no, member_id=m.member_id, claimant_name=m.name,
               claim_type=body.claim_type, incident_date=body.admission_date.isoformat(),
               admission_date=body.admission_date.isoformat() if body.claim_type in ("INPATIENT", "DAYCARE") else None,
               discharge_date=body.discharge_date.isoformat() if body.discharge_date else None,
               provider_id=body.provider_id, requested_amount=0, submitted_date=date.today().isoformat(),
               payout_account=body.payout_account)
    batch = ingest_records(session, "claims", [row], "portal", "portal", source="portal")
    if batch.accepted == 0:
        errs = session.exec(select(IngestionError).where(IngestionError.batch_id == batch.id)).all()
        raise HTTPException(422, "; ".join(f"{e.field}: {e.message}" for e in errs if e.severity == "ERROR"))
    c = session.get(Claim, row["claim_id"])
    c.state = "DRAFT"
    session.add(c)
    audit.record(session, action="CLAIM_STARTED", entity="claim", entity_id=c.claim_id, claim_id=c.claim_id, actor=user,
                 after={"member_id": c.member_id, "provider_id": c.provider_id, "description": body.description})
    session.commit()
    return claim_view(c.claim_id, user, session)


def _save_upload(session: Session, user: User, claim: Claim, file: UploadFile, data: bytes, doc_type: str) -> Document:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in MIME:
        raise HTTPException(422, "Upload a PDF, PNG or JPG")
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    doc = Document(filename=file.filename, content_type=MIME[ext], path="", claim_id=claim.claim_id, doc_type=doc_type,
                   source="portal", uploaded_by=user.username)
    session.add(doc)
    session.flush()
    path = settings.upload_dir / f"{doc.id:05d}{ext}"
    path.write_bytes(data)
    doc.path = str(path)
    try:
        ext_ = extract(data, doc.content_type)
        doc.extraction, doc.status = ext_.model_dump(), "EXTRACTED"
    except Exception as e:  # no key / model error: the document is still on file for staff
        doc.status, doc.error = "FAILED", str(e)[:300]
    session.add(doc)
    audit.record(session, action="DOCUMENT_UPLOADED", entity="document", entity_id=doc.id, claim_id=claim.claim_id,
                 actor=user, after={"filename": doc.filename, "type": doc_type, "extraction": doc.status})
    return doc


def _scan_later(session: Session, doc: Document, tasks: BackgroundTasks) -> None:
    scan = DocumentScan(doc_id=doc.id)
    session.add(scan)
    session.flush()
    tasks.add_task(run_scan, scan.id)  # integrity agent runs silently; results are staff-only


@router.post("/claims/{claim_id}/documents", dependencies=[Depends(require("claim:file_own"))])
async def upload_document(claim_id: str, tasks: BackgroundTasks, file: UploadFile = File(...),
                          doc_type: str = Form("INVOICE"), user: User = PH, session: Session = Depends(get_session)):
    c = _own(session, user, claim_id)
    if c.state != "DRAFT":
        raise HTTPException(409, "Documents for a submitted claim are sent through an information request")
    doc = _save_upload(session, user, c, file, await file.read(), doc_type.upper())
    _scan_later(session, doc, tasks)
    session.commit()
    return claim_view(claim_id, user, session)


class SubmitLine(BaseModel):
    description: str
    service_code: str | None = None
    category: str = "other"
    date: date
    quantity: float = 1
    unit_price: float
    amount: float


class SubmitBody(BaseModel):
    lines: list[SubmitLine]
    invoice_no: str | None = None
    diagnosis_code: str | None = None
    declaration: bool


@router.post("/claims/{claim_id}/submit", dependencies=[Depends(require("claim:file_own"))])
def submit(claim_id: str, body: SubmitBody, user: User = PH, session: Session = Depends(get_session)):
    c = _own(session, user, claim_id)
    if c.state != "DRAFT":
        raise HTTPException(409, "Already submitted")
    if not body.declaration:
        raise HTTPException(422, "Please confirm the declaration")
    if not body.lines:
        raise HTTPException(422, "Add the items on your bill")
    rows = [dict(line_id=f"{claim_id}-L{i:02d}", claim_id=claim_id, invoice_no=body.invoice_no, provider_id=c.provider_id,
                 service_date=l.date.isoformat(), service_code=l.service_code or f"ITEM-{i:02d}", description=l.description,
                 category=l.category, quantity=l.quantity, unit_price=l.unit_price, amount=l.amount)
            for i, l in enumerate(body.lines, 1)]
    batch = ingest_records(session, "invoice_lines", rows, "portal", "portal")
    if batch.rejected:
        errs = session.exec(select(IngestionError).where(IngestionError.batch_id == batch.id)).all()
        raise HTTPException(422, "; ".join(f"line {e.row_number} {e.field}: {e.message}" for e in errs if e.severity == "ERROR"))
    c.requested_amount = round(sum(l.amount for l in body.lines), 2)
    c.proposed_settlement = c.requested_amount  # starting point: pay as claimed; the engine shows what to correct
    c.invoice_no = body.invoice_no or c.invoice_no
    c.diagnosis_code = body.diagnosis_code or c.diagnosis_code
    session.add(c)
    for doc in session.exec(select(Document).where(Document.claim_id == claim_id, Document.status == "EXTRACTED")):
        attach_llm_evidence(session, doc, doc.extraction)
    transition(session, c, "SUBMITTED", user, "submitted by policyholder")
    session.commit()
    run_analysis(session)
    route_after_analysis(session)
    session.commit()
    return claim_view(claim_id, user, session)


# ---------------------------------------------------------------- requests & reconsideration
@router.post("/requests/{req_id}/respond", dependencies=[Depends(require("evidence:respond_own"))])
async def respond(req_id: int, tasks: BackgroundTasks, message: str = Form(""), files: list[UploadFile] = File(default=[]),
                  user: User = PH, session: Session = Depends(get_session)):
    req = session.get(EvidenceRequest, req_id)
    if not req:
        raise HTTPException(404, "Request not found")
    c = _own(session, user, req.claim_id)
    if req.status != "OPEN":
        raise HTTPException(409, "This request is already answered")
    if not files and not message.strip():
        raise HTTPException(422, "Add a document or a message")
    doc_ids = []
    for f in files:
        doc = _save_upload(session, user, c, f, await f.read(), (req.doc_types or ["OTHER"])[0])
        if doc.extraction:
            attach_llm_evidence(session, doc, doc.extraction)
        _scan_later(session, doc, tasks)
        doc_ids.append(doc.id)
    resp = EvidenceResponse(request_id=req.id, claim_id=c.claim_id, responder=user.username, message=message.strip() or None,
                            doc_ids=doc_ids)
    req.status = "RESPONDED"
    session.add_all([resp, req])
    session.flush()
    audit.record(session, action="EVIDENCE_PROVIDED", entity="evidence_request", entity_id=req.id, claim_id=c.claim_id,
                 actor=user, after={"message": resp.message, "documents": doc_ids})
    still_open = session.exec(select(EvidenceRequest).where(EvidenceRequest.claim_id == c.claim_id,
                                                            EvidenceRequest.status == "OPEN")).first()
    if c.state == "INFO_REQUESTED" and not still_open:
        transition(session, c, "INVESTIGATION", "system:portal", "policyholder responded", check=False)
    if c.assigned_to:
        notify(session, f"{c.claim_id}: policyholder responded to your request", kind="evidence_response",
               claim_id=c.claim_id, username=c.assigned_to)
    session.commit()
    return claim_view(c.claim_id, user, session)


class ReconsiderBody(BaseModel):
    reason: str


@router.post("/claims/{claim_id}/reconsideration", dependencies=[Depends(require("claim:file_own"))])
def reconsider(claim_id: str, body: ReconsiderBody, user: User = PH, session: Session = Depends(get_session)):
    c = _own(session, user, claim_id)
    if not body.reason.strip():
        raise HTTPException(422, "Tell us why you disagree")
    transition(session, c, "RECONSIDERATION", user, body.reason.strip())
    transition(session, c, "INVESTIGATION", "system:portal", "reconsideration requested", check=False)
    if c.assigned_to:
        notify(session, f"{c.claim_id}: policyholder asked for reconsideration", kind="reconsideration",
               claim_id=c.claim_id, username=c.assigned_to)
    session.commit()
    return claim_view(claim_id, user, session)


@router.get("/claims/{claim_id}/letter.pdf", dependencies=[PH])
def letter_pdf(claim_id: str, user: User = PH, session: Session = Depends(get_session)):
    c = _own(session, user, claim_id)
    lt = _letter(session, c)
    if lt is None:
        raise HTTPException(404, "No decision yet")
    st = getSampleStyleSheet()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm)
    rs = lambda x: "-" if x is None else f"Rs. {x:,.2f}"  # noqa: E731
    rows = [["", "Amount"], ["Amount claimed", rs(lt["amount_claimed"])]]
    rows += [[a["explanation"], f"- {rs(a['amount_not_payable'])}"] for a in lt["adjustments"]]
    rows += [["Approved amount", rs(lt["approved_amount"])]]
    t = Table([[Paragraph(str(a), st["BodyText"]), b] for a, b in rows], colWidths=[130 * mm, 40 * mm])
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, "black"), ("LINEABOVE", (0, -1), (-1, -1), 0.8, "black"),
                           ("ALIGN", (1, 0), (1, -1), "RIGHT")]))
    story = [Paragraph("Claim decision", st["Title"]), Paragraph(f"Claim {c.claim_id} · {c.claimant_name} · policy {c.policy_no}", st["Normal"]),
             Spacer(1, 6 * mm), Paragraph(f"<b>{lt['decision']}</b>", st["Heading2"]), t, Spacer(1, 6 * mm),
             Paragraph(f"Payment account: {lt['payout_account'] or 'registered account'} · {'Paid' if lt['paid'] else 'Payment in process'}", st["Normal"]),
             Spacer(1, 4 * mm), Paragraph("If you disagree with this decision you can request reconsideration from your claims page.", st["Italic"])]
    doc.build(story)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{claim_id}_decision.pdf"'})
