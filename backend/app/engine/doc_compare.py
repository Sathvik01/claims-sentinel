"""Claim record vs its supporting documents (bill, discharge summary).

Every comparison row cites the document field it used (doc, page, source text). Differences that change what is
payable become settlement deductions — only when the document value is verified — so the reconciler attributes
them like any other policy deduction. Other differences (dates, diagnosis) are suspected findings with no amount."""

from __future__ import annotations

from datetime import date

from rapidfuzz import fuzz

from app.engine import base as B
from app.engine.base import Deduction, FindingDraft, inr
from app.models import Claim, ExtractedField, InvoiceLine

CLAUSE = "Claims procedure Sec 9.2 (settle on original documents)"


def _ref(f: ExtractedField | None) -> dict | None:
    if f is None:
        return None
    return {"doc_id": f.doc_id, "doc_type": f.doc_type, "path": f.path, "page": f.page, "source_text": f.source_text,
            "verified": f.verified, "method": f.method}


def _num(f: ExtractedField | None) -> float | None:
    try:
        return float(f.value) if f is not None else None
    except ValueError:
        return None


def _match_lines(lines: list[InvoiceLine], doc_lines: list[dict]) -> tuple[list[tuple], list[InvoiceLine], list[dict]]:
    used: set[int] = set()
    pairs, claim_only = [], []
    for ln in sorted(lines, key=lambda l: l.line_id):
        best = None
        for j, d in enumerate(doc_lines):
            if j in used or "date" not in d or d["date"].value != ln.service_date.isoformat():
                continue
            same_code = "code" in d and d["code"].value == ln.service_code
            similar = "description" in d and fuzz.token_sort_ratio(d["description"].value, ln.description) >= 85
            if same_code or similar:
                best = j
                break
        if best is None:
            claim_only.append(ln)
        else:
            used.add(best)
            pairs.append((ln, doc_lines[best]))
    doc_only = [d for j, d in enumerate(doc_lines) if j not in used]
    return pairs, claim_only, doc_only


def compare(claim: Claim, lines: list[InvoiceLine], ev: dict, remaining: dict[str, float]) \
        -> tuple[list[dict], list[Deduction], list[FindingDraft]]:
    rows: list[dict] = []
    deductions: list[Deduction] = []
    drafts: list[FindingDraft] = []
    inv, dis = ev.get("INVOICE"), ev.get("DISCHARGE_SUMMARY")

    def take(ln: InvoiceLine, amount: float) -> float:
        amt = round(min(amount, remaining.get(ln.line_id, 0)), 2)
        remaining[ln.line_id] = round(remaining.get(ln.line_id, 0) - amt, 2)
        return amt

    # --- header fields
    header_checks = [("invoice_no", claim.invoice_no, inv), ("member_id", claim.member_id, inv),
                     ("admission_date", claim.admission_date, dis or inv),
                     ("discharge_date", claim.discharge_date, dis or inv), ("diagnosis", claim.diagnosis_code, dis or inv)]
    date_diffs = []
    for key, claim_val, doc in header_checks:
        f = doc["header"].get(key) if doc else None
        if f is None:
            continue
        cv = claim_val.isoformat() if isinstance(claim_val, date) else claim_val
        status = "MATCH" if str(cv or "") == f.value else "DIFF"
        rows.append({"section": "header", "field": key, "claim_value": cv, "doc_value": f.value, "status": status,
                     "source": _ref(f)})
        if status == "DIFF" and key in ("admission_date", "discharge_date", "diagnosis", "invoice_no"):
            date_diffs.append((key, cv, f))

    # --- itemised lines against the bill
    if inv and inv["lines"]:
        pairs, claim_only, doc_only = _match_lines(lines, inv["lines"])
        verified_bill = all(f.verified for d in inv["lines"] for f in d.values())
        for ln, d in pairs:
            doc_amt = _num(d.get("amount"))
            status = "MATCH" if doc_amt is not None and abs(ln.amount - doc_amt) <= 1 else "AMOUNT_DIFF"
            rows.append({"section": "lines", "field": f"{ln.service_code} {ln.service_date}", "line_id": ln.line_id,
                         "claim_value": ln.amount, "doc_value": doc_amt, "status": status, "source": _ref(d.get("amount"))})
            if status == "AMOUNT_DIFF" and doc_amt is not None and ln.amount > doc_amt and d["amount"].verified:
                amt = take(ln, ln.amount - doc_amt)
                if amt > 0:
                    deductions.append(Deduction(
                        f"E2:{ln.line_id}", "E2", B.DOCUMENT_MISMATCH, CLAUSE, f"Claimed above the bill: {ln.description}",
                        amt, f"Claim line {inr(ln.amount)} vs hospital bill {inr(doc_amt)} for {ln.description} on "
                             f"{ln.service_date:%d-%b-%Y} (bill page {d['amount'].page + 1}: \"{d['amount'].source_text}\") "
                             f"→ excess {inr(amt)}.", [ln.line_id],
                        evidence={"source": _ref(d["amount"]), "claim_amount": ln.amount, "document_amount": doc_amt}))
        for ln in claim_only:
            rows.append({"section": "lines", "field": f"{ln.service_code} {ln.service_date}", "line_id": ln.line_id,
                         "claim_value": ln.amount, "doc_value": None, "status": "CLAIM_ONLY",
                         "source": {"doc_id": inv["doc_id"], "doc_type": "INVOICE", "verified": verified_bill}})
            if verified_bill and remaining.get(ln.line_id, 0) > 0:
                amt = take(ln, remaining[ln.line_id])
                deductions.append(Deduction(
                    f"E1:{ln.line_id}", "E1", B.DOCUMENT_MISMATCH, CLAUSE, f"Not on the hospital bill: {ln.description}",
                    amt, f"{ln.description} ({ln.service_code}) on {ln.service_date:%d-%b-%Y} for {inr(ln.amount)} is "
                         f"claimed but does not appear among the {len(inv['lines'])} items on the hospital bill.",
                    [ln.line_id], evidence={"document_id": inv["doc_id"], "bill_items": len(inv["lines"])}))
        for d in doc_only:
            rows.append({"section": "lines", "field": f"{d['code'].value if 'code' in d else '?'} {d['date'].value if 'date' in d else ''}",
                         "claim_value": None, "doc_value": _num(d.get("amount")), "status": "DOC_ONLY",
                         "source": _ref(d.get("amount"))})

    # --- length of stay (discharge summary) vs room/ICU days billed in the claim
    los_f = dis["header"].get("length_of_stay") if dis else None
    if los_f is not None and los_f.verified:
        los = int(los_f.value)
        stay_lines = [l for l in lines if l.category in ("room", "icu")]
        billed_days = sum(l.quantity for l in stay_lines)
        rows.append({"section": "stay", "field": "room/ICU days", "claim_value": billed_days, "doc_value": los,
                     "status": "MATCH" if billed_days <= los else "DIFF", "source": _ref(los_f)})
        if billed_days > los and stay_lines:
            extra = billed_days - los
            ln = max((l for l in stay_lines if l.category == "room"), default=stay_lines[0], key=lambda l: l.quantity)
            amt = take(ln, min(extra, ln.quantity) * ln.unit_price)
            if amt > 0:
                deductions.append(Deduction(
                    f"E3:{claim.claim_id}", "E3", B.DOCUMENT_MISMATCH, CLAUSE, "Room days beyond the documented stay",
                    amt, f"Discharge summary: \"{los_f.source_text}\" (page {los_f.page + 1}); claim bills {billed_days:g} "
                         f"room/ICU day(s) → {extra:g} extra day(s) × {inr(ln.unit_price)} = {inr(amt)}.",
                    [ln.line_id], evidence={"source": _ref(los_f), "billed_days": billed_days, "length_of_stay": los}))

    if date_diffs:
        parts = [f"{k.replace('_', ' ')} {cv or '—'} in the claim vs {f.value} on the "
                 f"{f.doc_type.replace('_', ' ').lower()} (page {(f.page or 0) + 1})" for k, cv, f in date_diffs]
        drafts.append(FindingDraft(
            B.DOCUMENT_MISMATCH, "E4", "Claim details differ from the documents", B.SUSPECTED, B.NOT_DETERMINABLE,
            "; ".join(parts) + ". No amount is assumed; confirm which record is correct.", clause_ref=CLAUSE,
            confidence=0.7, evidence={"differences": [{"field": k, "claim_value": cv, "source": _ref(f)}
                                                      for k, cv, f in date_diffs]}))
    return rows, deductions, drafts
