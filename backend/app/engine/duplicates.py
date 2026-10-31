"""Duplicate detection: exact claims (D1), lines within a claim (D2), lines across claims (D3),
near-duplicate claims (D4) and the legitimate-repeat guard."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.config import settings
from app.engine.base import (
    DUPLICATE_CLAIM,
    DUPLICATE_LINE,
    ESTIMATED,
    NEAR_DUPLICATE,
    REPEATABLE_CATEGORIES,
    SUSPECTED,
    Context,
    Deduction,
    FindingDraft,
    inr,
    norm_invoice,
    norm_invoice_loose,
)
from app.models import Claim, InvoiceLine


def _line_sig(ln: InvoiceLine) -> tuple:
    return (ln.service_code, ln.service_date, round(ln.amount, 2))


def _claim_date(c: Claim):
    return c.admission_date or c.incident_date


def _amount(c: Claim) -> float:
    return c.requested_amount or 0.0


class LineIndex:
    """(member, provider, code, date, amount) → [(claim_id, line_id)] across every claim on file."""

    def __init__(self, ctx: Context):
        self.idx: dict[tuple, list[tuple[str, str]]] = defaultdict(list)
        for cid, lines in ctx.lines_by_claim.items():
            claim = ctx.claims.get(cid)
            if not claim:
                continue
            for ln in lines:
                self.idx[(claim.member_id, ln.provider_id, *_line_sig(ln))].append((cid, ln.line_id))


def exact_duplicate_claim(claim: Claim, ctx: Context, billed: float) -> Deduction | None:
    inv = norm_invoice(claim.invoice_no)
    sig = sorted(_line_sig(l) for l in ctx.lines_by_claim.get(claim.claim_id, []))
    for other in ctx.claims_by_member.get(claim.member_id, []):
        if other.claim_id == claim.claim_id or other.provider_id != claim.provider_id:
            continue
        if not ctx.is_earlier(other, claim):
            continue
        same_invoice = inv is not None and inv == norm_invoice(other.invoice_no)
        same_incident = (other.incident_date == claim.incident_date and sig and
                         sorted(_line_sig(l) for l in ctx.lines_by_claim.get(other.claim_id, [])) == sig)
        if not (same_invoice or same_incident):
            continue
        basis = []
        if same_invoice:
            basis.append(f"same invoice {claim.invoice_no}")
        if same_incident:
            basis.append(f"identical incident ({claim.incident_date:%d-%b-%Y}) and identical line items")
        paid = ctx.paid_amount(other.claim_id)
        prior = (f"already paid {inr(paid)} on {other.claim_id}" if other.status == "PAID"
                 else f"earlier submission {other.claim_id} pending")
        return Deduction(
            f"D1:{claim.claim_id}", "D1", DUPLICATE_CLAIM, "Claims procedure Sec 9.4 (one payment per incident)",
            f"Duplicate of claim {other.claim_id}", billed,
            f"{'; '.join(basis).capitalize()} as {other.claim_id} ({prior}).",
            [l.line_id for l in ctx.lines_by_claim.get(claim.claim_id, [])], related_claim_id=other.claim_id,
            evidence={"matched_claim": other.claim_id, "matched_status": other.status, "basis": basis,
                      "matched_paid_amount": paid, "matched_invoice": other.invoice_no,
                      "matched_submitted": str(other.submitted_date)},
        )
    return None


def duplicate_lines(claim: Claim, ctx: Context, lines: list[InvoiceLine], remaining: dict[str, float],
                    index: LineIndex, notes: dict[str, str], cleared: list[dict]) -> list[Deduction]:
    out: list[Deduction] = []

    # D2: identical charge repeated within the same invoice
    groups: dict[tuple, list[InvoiceLine]] = defaultdict(list)
    for ln in lines:
        groups[(ln.service_code, ln.service_date, ln.unit_price, ln.quantity)].append(ln)
    for (code, d, price, qty), grp in groups.items():
        if len(grp) < 2:
            continue
        grp = sorted(grp, key=lambda l: l.line_id)
        for dup in grp[1:]:
            amt = round(remaining[dup.line_id], 2)
            if amt <= 0:
                continue
            remaining[dup.line_id] = 0.0
            out.append(Deduction(
                f"D2:{dup.line_id}", "D2", DUPLICATE_LINE, "Invoice validation (each service payable once)",
                f"Duplicate line: {dup.description}", amt,
                f"{dup.description} ({code}) on {d:%d-%b-%Y} billed {len(grp)}× at {inr(price)} × {qty:g}; "
                f"repeat line {dup.line_id} = {inr(amt)}.",
                [grp[0].line_id, dup.line_id],
                evidence={"original_line": grp[0].line_id, "duplicate_line": dup.line_id, "service_date": str(d)}))
    # legitimate repeats inside the claim: same code on distinct dates
    by_code: dict[str, set] = defaultdict(set)
    cat_of: dict[str, str] = {}
    for ln in lines:
        by_code[ln.service_code].add(ln.service_date)
        cat_of[ln.service_code] = ln.category
    for code, dates in by_code.items():
        if len(dates) > 1 and cat_of[code] in REPEATABLE_CATEGORIES:
            cleared.append({"check": "D2", "detail": f"{code} billed on {len(dates)} distinct dates — "
                                                     f"recurring {cat_of[code]} service, not treated as duplicate"})

    # D3: same charge already billed on another (earlier) claim of the member
    for ln in lines:
        if remaining[ln.line_id] <= 0:
            continue
        for other_cid, other_lid in index.idx.get((claim.member_id, ln.provider_id, *_line_sig(ln)), []):
            other = ctx.claims[other_cid]
            if other_cid == claim.claim_id or not ctx.is_earlier(other, claim):
                continue
            amt = round(remaining[ln.line_id], 2)
            remaining[ln.line_id] = 0.0
            out.append(Deduction(
                f"D3:{ln.line_id}", "D3", DUPLICATE_LINE, "Invoice validation (each service payable once)",
                f"Line already billed on {other_cid}: {ln.description}", amt,
                f"{ln.description} on {ln.service_date:%d-%b-%Y} for {inr(ln.amount)} matches line {other_lid} "
                f"of {other_cid} ({other.status.lower()}) — same member, provider, code, date and amount.",
                [ln.line_id], related_claim_id=other_cid,
                evidence={"matched_claim": other_cid, "matched_line": other_lid, "matched_status": other.status}))
            break
    notes["D2"] = "No repeated charges within the invoice"
    notes["D3"] = "No charges matching other claims"
    return out


@dataclass
class NearDupScore:
    total: float
    parts: dict


def _score(a: Claim, b: Claim, la: list[InvoiceLine], lb: list[InvoiceLine]) -> NearDupScore | None:
    days = abs((_claim_date(a) - _claim_date(b)).days)
    amt_a, amt_b = _amount(a), _amount(b)
    pct = abs(amt_a - amt_b) / max(amt_a, amt_b, 1) * 100
    codes_a, codes_b = {l.service_code for l in la}, {l.service_code for l in lb}
    jacc = len(codes_a & codes_b) / max(len(codes_a | codes_b), 1)
    if days > settings.near_dup_max_days or pct > settings.near_dup_max_amount_pct or jacc < 0.5:
        return None
    inv = fuzz.ratio(norm_invoice_loose(a.invoice_no), norm_invoice_loose(b.invoice_no)) / 100 \
        if a.invoice_no and b.invoice_no else 0.5
    parts = {
        "invoice_similarity": round(inv, 2),
        "date_proximity": round(1 - days / (settings.near_dup_max_days + 1), 2),
        "amount_similarity": round(max(0.0, 1 - pct / 5), 2),
        "same_provider": 1.0 if a.provider_id == b.provider_id else 0.0,
        "same_diagnosis": 1.0 if a.diagnosis_code and a.diagnosis_code == b.diagnosis_code else 0.0,
        "line_overlap": round(jacc, 2),
    }
    weights = {"invoice_similarity": .25, "date_proximity": .25, "amount_similarity": .2,
               "same_provider": .1, "same_diagnosis": .1, "line_overlap": .1}
    total = sum(parts[k] * w for k, w in weights.items())
    return NearDupScore(round(total, 3), parts | {"days_apart": days, "amount_diff_pct": round(pct, 2)})


def near_duplicates(claim: Claim, ctx: Context, exact_match: str | None, cleared: list[dict]) -> list[FindingDraft]:
    lines = ctx.lines_by_claim.get(claim.claim_id, [])
    out = []
    for other in ctx.claims_by_member.get(claim.member_id, []):
        if other.claim_id in (claim.claim_id, exact_match) or not ctx.is_earlier(other, claim):
            continue
        olines = ctx.lines_by_claim.get(other.claim_id, [])
        sc = _score(claim, other, lines, olines)
        if sc is None:
            continue
        repeatable = all(l.category in REPEATABLE_CATEGORIES for l in lines + olines)
        disjoint = not ({l.service_date for l in lines} & {l.service_date for l in olines})
        if repeatable and disjoint:
            cleared.append({"check": "D4", "detail": f"Similar to {other.claim_id} (score {sc.total:.2f}) but "
                                                     "services are recurring and on separate dates — "
                                                     "treated as a legitimate repeat service",
                            "related_claim_id": other.claim_id})
            continue
        if sc.total < settings.near_dup_threshold:
            continue
        est = claim.proposed_settlement if claim.proposed_settlement is not None else claim.requested_amount
        out.append(FindingDraft(
            category=NEAR_DUPLICATE, rule_id="D4", title=f"Possible resubmission of {other.claim_id}",
            finding_type=SUSPECTED, amount_basis=ESTIMATED,
            clause_ref="Claims procedure Sec 9.4 (one payment per incident)",
            billed_excess=claim.requested_amount, leakage_amount=est, related_claim_id=other.claim_id,
            confidence=sc.total,
            calculation=(f"Similarity {sc.total:.2f} with {other.claim_id}: {sc.parts['days_apart']} day(s) apart, "
                         f"amounts differ {sc.parts['amount_diff_pct']}%, invoice {claim.invoice_no or '—'} vs "
                         f"{other.invoice_no or '—'}. If it is the same incident, the full proposed "
                         f"{inr(est)} would be paid twice (estimate, needs review)."),
            evidence={"matched_claim": other.claim_id, "matched_status": other.status, "score": sc.total,
                      "score_parts": sc.parts},
        ))
    return out
