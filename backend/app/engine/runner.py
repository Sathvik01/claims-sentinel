"""Runs every detector over the pending claims and persists findings + per-claim assessments."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, delete, select

from app.config import settings
from app.engine import base as B
from app.engine.base import Check, Context, Deduction, FindingDraft, inr
from app.engine.duplicates import LineIndex, duplicate_lines, exact_duplicate_claim, near_duplicates
from app.engine.provider_outliers import build_outliers, provider_outlier_findings
from app.engine.rules import RULE_FAMILIES, admissibility, completeness, line_deductions
from app.engine.settlement import Terms, finalize, reconcile
from app.models import AnalysisRun, Claim, ClaimAssessment, Finding, FindingAction

FAMILY_BY_CATEGORY = {
    B.COVERAGE: "R01", B.WAITING_PERIOD: "R02", B.EXCLUDED_DIAGNOSIS: "R03", B.EXCLUDED_SERVICE: "R03",
    B.ROOM_RENT_LIMIT: "R04", B.ICU_LIMIT: "R05", B.SUB_LIMIT: "R06", B.FREQUENCY_CAP: "R06",
    B.ARITHMETIC_ERROR: "R10", B.DUPLICATE_CLAIM: "D1",
}
WHOLE_CLAIM_CATEGORIES = {B.DUPLICATE_CLAIM, B.COVERAGE, B.WAITING_PERIOD, B.EXCLUDED_DIAGNOSIS}


def _family(d: Deduction) -> str:
    return d.rule_id if d.rule_id in ("D2", "D3") else FAMILY_BY_CATEGORY.get(d.category, d.rule_id)


def severity(f: FindingDraft) -> str:
    amt = f.leakage_amount or 0
    if f.finding_type == B.NEEDS_INFO:
        return "LOW"
    if f.finding_type == B.CONFIRMED and (f.category in WHOLE_CLAIM_CATEGORIES or amt >= settings.severity_high):
        return "HIGH"
    if amt >= settings.severity_high and f.confidence >= 0.8:
        return "HIGH"
    if amt >= settings.severity_medium or (f.finding_type == B.SUSPECTED and f.confidence >= 0.85):
        return "MEDIUM"
    return "LOW"


def _deduction_finding(d: Deduction, impact: float | None, note: str) -> FindingDraft:
    calc = d.calculation
    if impact is not None:
        calc += f" {note} {inr(impact)}."
    return FindingDraft(
        category=d.category, rule_id=d.rule_id, title=d.title, finding_type=B.CONFIRMED,
        amount_basis=B.CALCULATED, calculation=calc, clause_ref=d.clause_ref, billed_excess=d.amount,
        leakage_amount=impact, line_ids=d.line_ids, related_claim_id=d.related_claim_id, evidence=d.evidence,
    )


def assess_claim(claim: Claim, ctx: Context, index: LineIndex, outliers: dict) -> tuple[list[FindingDraft], dict]:
    lines = sorted(ctx.lines_by_claim.get(claim.claim_id, []), key=lambda l: l.line_id)
    drafts, flags = completeness(claim, lines)
    notes: dict[str, str] = {}
    cleared: list[dict] = []
    billed = round(sum(l.amount for l in lines), 2) if lines else claim.requested_amount
    policy = ctx.policies.get(claim.policy_no)
    if policy is None:
        drafts.append(FindingDraft(B.INCOMPLETE_EVIDENCE, "C02", f"Policy {claim.policy_no} not on file",
                                   B.NEEDS_INFO, B.NOT_DETERMINABLE,
                                   "Policy terms are required to validate the claim; no amount assumed.",
                                   evidence={"clarification_request": f"Provide policy schedule for {claim.policy_no}."}))
        return drafts, {"billed_amount": billed, "checks": [], "cleared": [], "waterfall": []}

    remaining = {l.line_id: l.amount for l in lines}
    whole = admissibility(claim, ctx, policy, flags, billed, notes)
    dup_claim = None
    if whole is None and lines:
        dup_claim = exact_duplicate_claim(claim, ctx, billed)
        whole = dup_claim
    if whole:
        # Line rules first, so the whole-claim deduction is exactly what would otherwise be paid.
        deductions = line_deductions(claim, ctx, policy, lines, remaining, notes) if lines else []
        whole.amount = round(sum(remaining.values()), 2) if lines else billed
        whole.calculation += f" Remaining admissible amount {inr(whole.amount)} is not payable."
        for k in remaining:
            remaining[k] = 0.0
        deductions.append(whole)
        for fam in RULE_FAMILIES:
            notes.setdefault(fam, "NA: claim inadmissible as a whole")
    else:
        notes["D1"] = "No matching invoice or incident on file"
        deductions = duplicate_lines(claim, ctx, lines, remaining, index, notes, cleared)
        deductions += line_deductions(claim, ctx, policy, lines, remaining, notes)

    terms = Terms(policy.deductible, policy.copay_pct,
                  round(policy.sum_insured - ctx.policy_year_paid(policy), 2))
    rec = reconcile(billed, deductions, terms, claim.proposed_settlement, settings.amount_tolerance)

    mode_errors: list[str] = []
    if claim.proposed_settlement is None:
        # Pre-payment view: what each deduction is worth if the claim were paid as billed.
        running_adm = billed - sum(d.amount for d in deductions)
        running = finalize(running_adm, terms)
        for d in deductions:
            running_adm += d.amount
            pay = finalize(running_adm, terms)
            drafts.append(_deduction_finding(d, round(pay - running, 2), "Payable impact if paid as billed:"))
            running = pay
    else:
        for a in rec.attributions:
            if a.deduction is not None:
                f = _deduction_finding(a.deduction, a.impact, "Overpayment in proposed settlement:")
                if not rec.explained:
                    f.evidence = f.evidence | {"allocation": "proportional (gap not reproducible by a single error)"}
            else:
                mode_errors.append(a.rule_id)
                f = FindingDraft(B.SETTLEMENT_MISCALC, a.rule_id, a.title, B.CONFIRMED, B.CALCULATED,
                                 a.calculation, clause_ref="Sec 8 (Settlement computation)",
                                 billed_excess=a.impact, leakage_amount=a.impact,
                                 evidence={"terms": terms.__dict__, "expected": rec.expected,
                                           "proposed": rec.proposed})
            f.evidence = f.evidence | {"reconciliation": rec.summary}
            drafts.append(f)

    if not dup_claim and not whole:
        drafts.extend(near_duplicates(claim, ctx, None, cleared))
    drafts.extend(provider_outlier_findings(claim, ctx, outliers))

    # rules-evaluated checklist
    checks: list[Check] = []
    for fam, name in RULE_FAMILIES.items():
        fam_deds = [d for d in deductions if _family(d) == fam]
        missed = [d for d in fam_deds if d.key in rec.missed_keys or claim.proposed_settlement is None]
        if fam in ("R07", "R08", "R09"):
            bad = any(fam in m for m in mode_errors)
            detail = {"R07": f"Deductible {inr(terms.deductible)} per claim",
                      "R08": f"Co-payment {terms.copay_pct:g}%",
                      "R09": f"Remaining sum insured {inr(terms.remaining_si)} of {inr(policy.sum_insured)}"}[fam]
            outcome = "FAIL" if bad else ("NA" if claim.proposed_settlement is None else "PASS")
            checks.append(Check(fam, name, outcome, detail))
        elif fam == "D4":
            hits = [f for f in drafts if f.category == B.NEAR_DUPLICATE]
            checks.append(Check(fam, name, "FAIL" if hits else ("NA" if whole else "PASS"),
                                f"Similar to {hits[0].related_claim_id}" if hits
                                else notes.get("D4", "No similar claims for this member within the window")))
        elif missed:
            checks.append(Check(fam, name, "FAIL", "; ".join(d.title for d in missed), missed[0].clause_ref))
        elif fam_deds:
            checks.append(Check(fam, name, "DEDUCTED",
                                "Correctly deducted in proposed settlement: " + "; ".join(d.title for d in fam_deds),
                                fam_deds[0].clause_ref))
        else:
            note = notes.get(fam, "Passed")
            checks.append(Check(fam, name, "NA" if note.startswith("NA") else "PASS", note))

    assessment = {
        "billed_amount": billed, "expected_settlement": rec.expected, "proposed_settlement": rec.proposed,
        "gap": rec.gap, "reconciliation": rec.summary, "waterfall": rec.waterfall,
        "checks": [c.as_dict() for c in checks], "cleared": cleared,
    }
    return drafts, assessment


def _key(f) -> tuple:
    return (f.claim_id, f.category, f.rule_id, tuple(sorted(f.line_ids or [])), f.related_claim_id)


def run_analysis(session: Session) -> AnalysisRun:
    ctx = Context.load(session)
    index = LineIndex(ctx)
    outliers = build_outliers(ctx)
    run = AnalysisRun()
    session.add(run)
    session.flush()

    previous = {_key(f): f for f in session.exec(select(Finding))}
    actions_by_finding: dict[int, list[FindingAction]] = {}
    for act in session.exec(select(FindingAction)):
        actions_by_finding.setdefault(act.finding_id, []).append(act)
    session.exec(delete(Finding))
    session.exec(delete(ClaimAssessment))

    count = 0
    pending = ctx.pending_claims()
    carried: list[tuple[int, Finding]] = []
    for claim in pending:
        drafts, a = assess_claim(claim, ctx, index, outliers)
        session.add(ClaimAssessment(claim_id=claim.claim_id, run_id=run.id, **a))
        for d in drafts:
            f = Finding(run_id=run.id, claim_id=claim.claim_id, severity=severity(d), **d.__dict__)
            old = previous.get(_key(f))
            if old is not None:  # keep reviewer decisions across re-runs
                f.status, f.corrected_amount, f.ai_explanation = old.status, old.corrected_amount, old.ai_explanation
                carried.append((old.id, f))
            session.add(f)
            count += 1
    session.flush()
    for old_id, f in carried:
        for act in actions_by_finding.pop(old_id, []):
            act.finding_id = f.id
    for orphans in actions_by_finding.values():  # findings that no longer reproduce
        for act in orphans:
            session.delete(act)
    run.claims_analyzed = len(pending)
    run.findings_count = count
    run.finished_at = datetime.now(UTC)
    session.commit()
    session.refresh(run)
    return run
