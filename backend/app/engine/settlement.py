"""Expected-settlement recalculation and reconciliation against the proposed amount.

The payable amount is: admissible (billed − policy deductions) → − deductible → − co-pay → capped at
remaining sum insured. Reconciliation searches for the simplest handler error that reproduces the
proposed amount exactly (which deductions were missed, and whether deductible / co-pay / SI cap were
skipped), so each rupee of overpayment is attributed to a concrete cause.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from app.engine.base import Deduction, inr

MAX_SEARCH_DEDUCTIONS = 12


@dataclass
class Terms:
    deductible: float
    copay_pct: float
    remaining_si: float


@dataclass
class Mode:
    """How the handler computed the settlement. `copay_pct` overrides the policy co-pay."""

    name: str = "correct"
    skip_deductible: bool = False
    copay_pct: float | None = None
    skip_cap: bool = False

    @property
    def is_error(self) -> bool:
        return self.name != "correct"


def finalize(admissible: float, terms: Terms, mode: Mode | None = None) -> float:
    mode = mode or Mode()
    admissible = max(admissible, 0.0)
    ded = 0.0 if mode.skip_deductible else min(terms.deductible, admissible)
    after = admissible - ded
    rate = terms.copay_pct if mode.copay_pct is None else mode.copay_pct
    payable = after - round(after * rate / 100, 2)
    if not mode.skip_cap:
        payable = min(payable, max(terms.remaining_si, 0.0))
    return round(payable, 2)


def finalize_steps(admissible: float, terms: Terms) -> list[dict]:
    admissible = max(admissible, 0.0)
    ded = min(terms.deductible, admissible)
    after = admissible - ded
    copay = round(after * terms.copay_pct / 100, 2)
    payable = after - copay
    capped = min(payable, max(terms.remaining_si, 0.0))
    return [
        {"label": f"Deductible ({inr(terms.deductible)} per claim)", "rule_id": "R07", "amount": -ded},
        {"label": f"Co-payment ({terms.copay_pct:g}%)", "rule_id": "R08", "amount": -copay},
        {"label": f"Sum insured cap (remaining {inr(terms.remaining_si)})", "rule_id": "R09",
         "amount": -round(payable - capped, 2)},
    ]


def _modes(terms: Terms) -> list[Mode]:
    modes = [Mode()]
    if terms.deductible > 0:
        modes.append(Mode("deductible_not_applied", skip_deductible=True))
    if terms.copay_pct > 0:
        modes.append(Mode("copay_not_applied", copay_pct=0))
        for pct in range(5, 55, 5):
            if pct != terms.copay_pct:
                modes.append(Mode(f"copay_wrong_rate_{pct}", copay_pct=pct))
    modes.append(Mode("si_cap_not_applied", skip_cap=True))
    if terms.deductible > 0 and terms.copay_pct > 0:
        modes.append(Mode("deductible_and_copay_not_applied", skip_deductible=True, copay_pct=0))
    return modes


MODE_TEXT = {
    "deductible_not_applied": ("R07", "Deductible not applied"),
    "copay_not_applied": ("R08", "Co-payment not applied"),
    "si_cap_not_applied": ("R09", "Sum insured cap not applied"),
    "deductible_and_copay_not_applied": ("R07+R08", "Deductible and co-payment not applied"),
}


@dataclass
class Attribution:
    deduction: Deduction | None  # None → settlement-mode error
    impact: float
    title: str
    rule_id: str
    calculation: str


@dataclass
class Reconciliation:
    billed: float
    expected: float
    proposed: float | None
    gap: float | None
    explained: bool
    summary: str
    attributions: list[Attribution] = field(default_factory=list)
    missed_keys: set[str] = field(default_factory=set)
    waterfall: list[dict] = field(default_factory=list)


def _mode_attribution(mode: Mode, terms: Terms, adm: float, base: float, proposed: float) -> Attribution:
    impact = round(proposed - base, 2)
    if mode.name.startswith("copay_wrong_rate_"):
        rule_id, title = "R08", f"Co-payment applied at {mode.copay_pct:g}% instead of {terms.copay_pct:g}%"
    else:
        rule_id, title = MODE_TEXT[mode.name]
    ded = min(terms.deductible, max(adm, 0))
    calc = (
        f"On the amount the handler treated as admissible ({inr(adm)}), policy terms (deductible "
        f"{inr(terms.deductible)}, co-pay {terms.copay_pct:g}%, remaining sum insured {inr(terms.remaining_si)}) "
        f"give {inr(base)} (({inr(adm)} − {inr(ded)}) × {100 - terms.copay_pct:g}%, capped). The proposed amount "
        f"is reproduced only if {title.lower()}: overpayment from this step {inr(impact)}."
    )
    return Attribution(None, impact, title, rule_id, calc)


def reconcile(
    billed: float, deductions: list[Deduction], terms: Terms, proposed: float | None, tol: float = 1.0
) -> Reconciliation:
    total_ded = sum(d.amount for d in deductions)
    admissible = billed - total_ded
    expected = finalize(admissible, terms)

    waterfall = [{"label": "Billed", "amount": billed, "kind": "total"}]
    for d in deductions:
        waterfall.append({"label": d.title, "rule_id": d.rule_id, "amount": -d.amount, "key": d.key})
    waterfall.append({"label": "Admissible", "amount": round(admissible, 2), "kind": "subtotal"})
    waterfall.extend(finalize_steps(admissible, terms))
    waterfall.append({"label": "Expected settlement", "amount": expected, "kind": "total"})

    if proposed is None:
        return Reconciliation(billed, expected, None, None, False,
                              "No proposed settlement supplied — deductions listed as pre-payment checks.",
                              waterfall=waterfall)
    waterfall.append({"label": "Proposed settlement", "amount": proposed, "kind": "proposed"})
    gap = round(proposed - expected, 2)
    if abs(gap) <= tol:
        return Reconciliation(billed, expected, proposed, gap, True,
                              "Proposed settlement matches the policy calculation.", waterfall=waterfall)
    if gap < 0:
        return Reconciliation(billed, expected, proposed, gap, True,
                              f"Proposed settlement is {inr(-gap)} below the policy calculation (possible underpayment).",
                              waterfall=waterfall)

    # Search for the simplest explanation: missed deductions S and a handler mode.
    searchable = sorted(deductions, key=lambda d: -d.amount)[:MAX_SEARCH_DEDUCTIONS]
    modes = _modes(terms)
    best: tuple[tuple[Deduction, ...], Mode] | None = None
    for complexity in range(1, len(searchable) + 2):
        for mode in modes:
            k = complexity - (1 if mode.is_error else 0)
            if k < 0:
                continue
            for subset in combinations(searchable, k):
                missed = sum(d.amount for d in subset)
                if abs(finalize(admissible + missed, terms, mode) - proposed) <= tol:
                    best = (subset, mode)
                    break
            if best:
                break
        if best:
            break

    attributions: list[Attribution] = []
    if best:
        subset, mode = best
        running_adm, running_pay = admissible, expected
        for d in subset:
            running_adm += d.amount
            pay = finalize(running_adm, terms)
            attributions.append(Attribution(d, round(pay - running_pay, 2), d.title, d.rule_id, d.calculation))
            running_pay = pay
        if mode.is_error:
            attributions.append(_mode_attribution(mode, terms, running_adm, running_pay, proposed))
        parts = [a.title for a in attributions]
        summary = (f"Proposed {inr(proposed)} vs expected {inr(expected)}: overpayment {inr(gap)}. "
                   f"The proposed amount is reproduced exactly if these were missed: {'; '.join(parts)}.")
        return Reconciliation(billed, expected, proposed, gap, True, summary, attributions,
                              {d.key for d in subset}, waterfall)

    # Unexplained gap: every deduction is a potential cause; the remainder is a residual finding.
    # Causes are valued before the sum-insured cap — otherwise an exhausted cover would value them all at ₹0.
    uncapped = Mode("uncapped", skip_cap=True)
    running_adm = admissible
    running_pay = finalize(admissible, terms, uncapped)
    for d in deductions:
        running_adm += d.amount
        pay = finalize(running_adm, terms, uncapped)
        attributions.append(Attribution(d, round(pay - running_pay, 2), d.title, d.rule_id, d.calculation))
        running_pay = pay
    explained = sum(a.impact for a in attributions)
    if explained > gap and explained > 0:
        scale = gap / explained
        for a in attributions:
            a.impact = round(a.impact * scale, 2)
    residual = round(gap - sum(a.impact for a in attributions), 2)
    if residual > tol:
        attributions.append(Attribution(None, residual, "Unexplained settlement difference", "R07-R09",
                                        f"Proposed {inr(proposed)} exceeds expected {inr(expected)} by {inr(gap)}; "
                                        f"{inr(residual)} is not explained by any single policy rule."))
    return Reconciliation(billed, expected, proposed, gap, False,
                          f"Proposed {inr(proposed)} exceeds expected {inr(expected)} by {inr(gap)}; "
                          "no single handler error reproduces it, so the gap is allocated across causes.",
                          attributions, {d.key for d in deductions}, waterfall)
