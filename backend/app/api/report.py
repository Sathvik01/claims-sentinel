"""Downloadable investigation report: structured JSON and a PDF built from the same data."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response, StreamingResponse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlmodel import Session

from app import audit
from app.api.investigations import workspace
from app.auth.deps import require
from app.db import get_session
from app.models import User

router = APIRouter(prefix="/api", tags=["report"])
ST = getSampleStyleSheet()
SMALL = ST["BodyText"].clone("small", fontSize=7.5, leading=9)


def _rs(x) -> str:
    return "-" if x is None else f"Rs. {x:,.2f}"


def build_report(claim_id: str, user: User, session: Session) -> dict:
    ws = workspace(claim_id, user, session)
    data = jsonable_encoder(ws)
    return {"report": "Claims Sentinel investigation report", "claim_id": claim_id,
            "generated_at": datetime.now(UTC).isoformat(), "generated_by": user.username,
            "audit_chain": audit.verify_chain(session), **data}


@router.get("/investigations/{claim_id}/report.json")
def report_json(claim_id: str, session: Session = Depends(get_session), user: User = Depends(require("report:export"))):
    body = json.dumps(build_report(claim_id, user, session), indent=2, ensure_ascii=False)
    return Response(body, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{claim_id}_investigation.json"'})


def _table(rows: list[list], widths: list[float]) -> Table:
    t = Table([[Paragraph(str(c), SMALL) for c in r] for r in rows], colWidths=[w * mm for w in widths], repeatRows=1)
    t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black), ("GRID", (0, 1), (-1, -1), 0.2, colors.grey),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]))
    return t


@router.get("/investigations/{claim_id}/report.pdf")
def report_pdf(claim_id: str, session: Session = Depends(get_session), user: User = Depends(require("report:export"))):
    r = build_report(claim_id, user, session)
    c, p = r["claim"], r.get("policy") or {}
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm)
    h = ST["Heading3"]
    story = [Paragraph(f"Investigation report — {claim_id}", ST["Title"]),
             Paragraph(f"Generated {r['generated_at'][:19]} UTC by {r['generated_by']} · audit chain "
                       f"{'verified' if r['audit_chain']['valid'] else 'BROKEN'} ({r['audit_chain']['events']} events)", SMALL),
             Spacer(1, 4 * mm), Paragraph("Claim and policy", h),
             _table([["Field", "Value"],
                     ["Claimant / member", f"{c['claimant_name']} · {c['member_id']}"],
                     ["Policy", f"{p.get('policy_no')} · {p.get('plan')} · {p.get('status')} · {p.get('start_date')} to {p.get('end_date')}"],
                     ["Provider / invoice", f"{c['provider_id']} · {c.get('invoice_no')}"],
                     ["Incident / stay", f"{c['incident_date']} · {c.get('admission_date')} to {c.get('discharge_date')} · {c.get('diagnosis_code')}"],
                     ["Requested / proposed", f"{_rs(c['requested_amount'])} / {_rs(c.get('proposed_settlement'))}"],
                     ["Corrected settlement", _rs(r.get("corrected_settlement"))],
                     ["State / assignee", f"{r['state']} · {r.get('assigned_to') or '-'}"]], [45, 135]),
             Spacer(1, 3 * mm), Paragraph("Findings, recommendations and decisions", h)]
    rows = [["Finding", "Recommendation (original)", "Decisions"]]
    for f in r["findings"]:
        recs = f.get("recommendations") or []
        first = recs[0] if recs else None
        rec_txt = (f"{first['type']}: {_rs(first['original_amount'])} → {_rs(first['corrected_amount'])}; impact "
                   f"{_rs(first['settlement_impact'])} ({first['rule_id']}{', ' + first['clause_ref'] if first.get('clause_ref') else ''})"
                   + (f" · {len(recs) - 1} later version(s)" if len(recs) > 1 else "")) if first else "-"
        dec = "; ".join(f"{d['decision']} by {d['user']} ({d['status']})"
                        + (f" override {_rs(d['override_impact'])}" if d.get("override_impact") is not None else "")
                        + (f": {d['reason']}" if d.get("reason") else "") for d in f.get("decisions") or []) or "-"
        rows.append([f"[{f['finding_type']}] {f['title']} — {f['calculation']}", rec_txt, f"{f['status']}. {dec}"])
    story += [_table(rows, [80, 55, 47])]
    if r["assessment"] and r["assessment"].get("documents"):
        story += [Spacer(1, 3 * mm), Paragraph("Claim vs documents", h),
                  _table([["Field", "Claim", "Document", "Status", "Source"]] +
                         [[d["field"], d.get("claim_value"), d.get("doc_value"), d["status"],
                           (f"{(d.get('source') or {}).get('doc_type', '')} p.{((d.get('source') or {}).get('page') or 0) + 1}: "
                            f"{(d.get('source') or {}).get('source_text') or ''}")] for d in r["assessment"]["documents"]
                          if d["status"] != "MATCH"] or [["-", "-", "-", "all values match", "-"]],
                         [32, 25, 25, 20, 80])]
    if r["related"]:
        story += [Spacer(1, 3 * mm), Paragraph("Related claims", h),
                  _table([["Claim", "Links"]] + [[x["claim_id"], "; ".join(f"{l['strength']} {l['attribute']}: {l['explanation']}"
                                                                         for l in x["links"])] for x in r["related"]], [30, 152])]
    story += [Spacer(1, 3 * mm), Paragraph("Evidence requests, escalations and notes", h),
              _table([["Type", "Detail", "Status"]] +
                     [["Evidence request", e["message"], e["status"]] for e in r["evidence_requests"]] +
                     [[f"Escalation → {e['target']}", f"{e['reason']} · {e.get('resolution') or ''} {e.get('outcome') or ''}", e["status"]] for e in r["escalations"]] +
                     [[f"Note ({n['visibility']})", f"{n['author']}: {n['text']}", ""] for n in r["notes"]] or [["-", "-", "-"]],
                     [35, 120, 27]),
              Spacer(1, 3 * mm), Paragraph("Audit trail", h),
              _table([["When (UTC)", "Actor", "Action", "Detail"]] +
                     [[e["at"][:19], f"{e['actor']} ({e['actor_role']})", e["action"],
                       (e.get("reason") or "") + (" " + json.dumps(e.get("after"))[:160] if e.get("after") else "")]
                      for e in r["timeline"]], [30, 32, 38, 82]),
              Spacer(1, 2 * mm), Paragraph(f"Chain head: {r['audit_chain'].get('head', '-')}", SMALL)]
    doc.build(story)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{claim_id}_investigation.pdf"'})
