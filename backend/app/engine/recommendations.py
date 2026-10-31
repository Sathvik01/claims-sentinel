"""Specific settlement corrections. Each confirmed, calculated finding becomes one typed recommendation with the
original amount, the corrected amount, the calculation and the policy clause — not a generic warning."""

from __future__ import annotations

from dataclasses import dataclass

from app.engine import base as B
from app.engine.base import Context, inr
from app.models import Claim, Finding, PolicyRule

BUILT_IN_CLAUSES = {
    "R01": "Cover applies only to insured members for incidents within the policy period while the policy is in force.",
    "R02": "Claims arising within the initial waiting period are not payable, except accidents.",
    "R04": "Room rent is payable up to the per-day limit in the policy schedule.",
    "R05": "ICU charges are payable up to the per-day limit in the policy schedule.",
    "R07": "The per-claim deductible is borne by the insured before any payment.",
    "R08": "The insured bears the co-payment percentage of the admissible amount after the deductible.",
    "R09": "Total payments in a policy year cannot exceed the sum insured.",
    "R10": "Each invoice line is payable at quantity × rate as itemised.",
    "D1": "One payment per incident; resubmitted bills are not payable again.",
    "D2": "Each service is payable once.",
    "D3": "Each service is payable once; charges already billed on another claim are not payable again.",
    "E1": "Settlement is made on the original itemised bill; items not on the bill are not payable.",
    "E2": "Settlement is made on the original itemised bill; claimed amounts above the bill are not payable.",
    "E3": "Room and ICU charges are payable for the documented length of stay only.",
}

TYPE_BY_CATEGORY = {
    B.DUPLICATE_LINE: "REMOVE_LINE", B.EXCLUDED_SERVICE: "REMOVE_EXCLUDED_ITEM", B.ROOM_RENT_LIMIT: "CAP_DAILY_RATE",
    B.ICU_LIMIT: "CAP_DAILY_RATE", B.SUB_LIMIT: "CAP_AT_SUBLIMIT", B.FREQUENCY_CAP: "LIMIT_SESSIONS",
    B.ARITHMETIC_ERROR: "CORRECT_ARITHMETIC", B.DOCUMENT_MISMATCH: "ALIGN_TO_DOCUMENT",
    B.COVERAGE: "REJECT_CLAIM", B.WAITING_PERIOD: "REJECT_CLAIM", B.EXCLUDED_DIAGNOSIS: "REJECT_CLAIM",
    B.DUPLICATE_CLAIM: "REJECT_CLAIM",
}
MODE_TYPE = {"R07": "APPLY_DEDUCTIBLE", "R08": "APPLY_COPAY", "R09": "APPLY_SI_CAP", "R07+R08": "APPLY_DEDUCTIBLE_AND_COPAY"}


@dataclass
class Draft:
    type: str
    action: str
    target_line_ids: list[str]
    original_amount: float
    corrected_amount: float
    settlement_impact: float | None
    calculation: str
    rule_id: str
    clause_ref: str | None
    clause_text: str | None

    @property
    def delta(self) -> float:
        return round(self.original_amount - self.corrected_amount, 2)

    def signature(self) -> tuple:
        return (self.type, self.original_amount, self.corrected_amount, self.settlement_impact, tuple(self.target_line_ids))


def _clause(rule_id: str, rules: dict[str, PolicyRule]) -> str | None:
    if rule_id in rules:
        return rules[rule_id].description
    return BUILT_IN_CLAUSES.get(rule_id.split("+")[0])


def recommend(f: Finding, claim: Claim, ctx: Context) -> Draft | None:
    if f.finding_type != B.CONFIRMED or f.amount_basis != B.CALCULATED or f.leakage_amount is None:
        return None
    rules = {r.rule_id: r for rs in ctx.rules_by_plan.values() for r in rs}
    lines = {l.line_id: l for l in ctx.lines_by_claim.get(claim.claim_id, [])}
    targets = [lines[i] for i in f.line_ids if i in lines]
    impact = round(f.leakage_amount, 2)
    clause = _clause(f.rule_id, rules)
    proposed = claim.proposed_settlement or 0.0

    if f.category == B.SETTLEMENT_MISCALC:
        t = MODE_TYPE.get(f.rule_id, "RECALCULATE_SETTLEMENT")
        return Draft(t, f"{f.title}: reduce the settlement by {inr(impact)}", [], proposed,
                     round(proposed - impact, 2), impact, f.calculation, f.rule_id, f.clause_ref, clause)
    t = TYPE_BY_CATEGORY.get(f.category)
    if t is None:
        return None
    if t == "REJECT_CLAIM":
        return Draft(t, f"Decline the claim: {f.title.lower()}", [l.line_id for l in targets], proposed, 0.0,
                     impact, f.calculation, f.rule_id, f.clause_ref, clause)

    excess = round(f.billed_excess or 0, 2)
    if f.category == B.DUPLICATE_LINE:  # target lists original + repeat; the repeat is removed
        dup = targets[-1:] if len(targets) > 1 else targets
        original = round(sum(l.amount for l in dup), 2)
        action = f"Remove the repeated line {dup[0].description} ({dup[0].line_id.split('-')[-1]})" if dup else f.title
        return Draft(t, action, [l.line_id for l in dup], original, round(original - excess, 2), impact,
                     f.calculation, f.rule_id, f.clause_ref, clause)
    original = round(sum(l.amount for l in targets), 2) if targets else excess
    corrected = round(original - excess, 2)
    verbs = {
        "REMOVE_EXCLUDED_ITEM": f"Remove the non-payable item {targets[0].description if targets else ''}",
        "CAP_DAILY_RATE": (f"Pay {targets[0].description.split(' (')[0].lower()} at the policy limit "
                           f"{inr(targets[0].unit_price - excess / max(targets[0].quantity, 1))}/day") if targets else f.title,
        "CAP_AT_SUBLIMIT": f"Cap at the sub-limit: pay {inr(corrected)} instead of {inr(original)}",
        "LIMIT_SESSIONS": f"Pay sessions within the yearly cap: {inr(corrected)} instead of {inr(original)}",
        "CORRECT_ARITHMETIC": f"Pay the line at quantity × rate: {inr(corrected)} instead of {inr(original)}",
        "ALIGN_TO_DOCUMENT": f"Pay as per the hospital documents: {inr(corrected)} instead of {inr(original)}",
    }
    return Draft(t, verbs.get(t, f.title), [l.line_id for l in targets], original, corrected, impact, f.calculation,
                 f.rule_id, f.clause_ref, clause)
