"""Policy rule validation: claim admissibility, completeness and line-level deductions."""

from __future__ import annotations

from collections import defaultdict

from app.engine.base import (
    ACCIDENT_ICD_PREFIXES,
    ARITHMETIC_ERROR,
    COVERAGE,
    EXCLUDED_DIAGNOSIS,
    EXCLUDED_SERVICE,
    FREQUENCY_CAP,
    ICU_LIMIT,
    INCOMPLETE_EVIDENCE,
    NEEDS_INFO,
    NOT_DETERMINABLE,
    ROOM_RENT_LIMIT,
    SUB_LIMIT,
    WAITING_PERIOD,
    Context,
    Deduction,
    FindingDraft,
    inr,
)
from app.models import Claim, InvoiceLine, Policy, PolicyRule

# rule family → display name (the "rules evaluated" checklist)
RULE_FAMILIES = {
    "R01": "Coverage status (policy active, member covered, incident within period)",
    "R02": "Waiting periods (initial and disease-specific)",
    "R03": "Exclusions (diagnosis and non-payable items)",
    "R04": "Room rent daily limit",
    "R05": "ICU daily limit",
    "R06": "Sub-limits and frequency caps",
    "R07": "Deductible",
    "R08": "Co-payment",
    "R09": "Sum insured / benefit cap",
    "R10": "Invoice arithmetic (qty × unit price)",
    "D1": "Duplicate claim / invoice",
    "D2": "Duplicate line within claim",
    "D3": "Duplicate line across claims",
    "D4": "Near-duplicate claim",
    "E1": "Claim vs documents (bill, discharge summary)",
}


def _matches(rule: PolicyRule, line: InvoiceLine) -> bool:
    if rule.target_type == "code":
        return line.service_code == rule.target
    if rule.target_type == "category":
        return line.category == rule.target
    return False


def completeness(claim: Claim, lines: list[InvoiceLine]) -> tuple[list[FindingDraft], set[str]]:
    """Essential information that, when missing, makes some checks unreliable → ask, don't guess."""
    missing: list[tuple[str, str, str]] = []
    flags: set[str] = set()
    if not claim.diagnosis_code:
        flags.add("no_diagnosis")
        missing.append(("diagnosis_code", "Diagnosis (ICD-10) code",
                        "exclusion and waiting-period rules cannot be evaluated"))
    if claim.claim_type in ("INPATIENT", "DAYCARE") and (not claim.admission_date or not claim.discharge_date):
        flags.add("no_stay_dates")
        missing.append(("admission/discharge_date", "Admission and discharge dates",
                        "length of stay cannot be verified against room/ICU days billed"))
    if not claim.invoice_no:
        flags.add("no_invoice_no")
        missing.append(("invoice_no", "Invoice reference number",
                        "invoice-level duplicate checks cannot be performed reliably"))
    if claim.proposed_settlement is None:
        flags.add("no_proposed")
        missing.append(("proposed_settlement", "Proposed settlement amount",
                        "the proposed payment cannot be reconciled"))
    if not lines:
        flags.add("no_lines")
        missing.append(("invoice_lines", "Itemised invoice lines", "no line-level validation is possible"))

    findings = []
    if missing:
        fields = [m[0] for m in missing]
        request = "Please provide: " + "; ".join(f"{m[1]} ({m[2]})" for m in missing) + "."
        findings.append(FindingDraft(
            category=INCOMPLETE_EVIDENCE, rule_id="C01",
            title="Clarification required: " + ", ".join(m[1] for m in missing),
            finding_type=NEEDS_INFO, amount_basis=NOT_DETERMINABLE,
            calculation="No amount is calculated: the affected checks were not run rather than assumed.",
            evidence={"missing_fields": fields, "clarification_request": request,
                      "impact": [m[2] for m in missing]},
        ))
    return findings, flags


def admissibility(claim: Claim, ctx: Context, policy: Policy, flags: set[str],
                  billed: float, notes: dict[str, str]) -> Deduction | None:
    """Claim-level rules (R01–R03). The first failure makes the whole claim inadmissible."""
    member = ctx.members.get(claim.member_id)
    rules = ctx.rules_by_plan.get(policy.plan, [])
    all_lines = [ln.line_id for ln in ctx.lines_by_claim.get(claim.claim_id, [])]

    # R01 coverage
    problems = []
    if member is None or member.policy_no != claim.policy_no:
        problems.append(f"member {claim.member_id} is not insured under policy {claim.policy_no}")
    if policy.status != "ACTIVE" and claim.incident_date > policy.end_date:
        problems.append(f"policy status {policy.status} (cover ended {policy.end_date:%d-%b-%Y})")
    if not (policy.start_date <= claim.incident_date <= policy.end_date):
        problems.append(f"incident date {claim.incident_date:%d-%b-%Y} outside policy period "
                        f"{policy.start_date:%d-%b-%Y} to {policy.end_date:%d-%b-%Y}")
    if problems:
        notes["R01"] = "; ".join(problems)
        return Deduction(f"R01:{claim.claim_id}", "R01", COVERAGE, "Sec 2.1 (Policy period & insured persons)",
                         "Claim not covered", billed,
                         f"Not covered: {'; '.join(problems)}.",
                         all_lines, evidence={"problems": problems, "policy_status": policy.status,
                                              "policy_start": str(policy.start_date),
                                              "policy_end": str(policy.end_date)})
    notes["R01"] = f"Policy {policy.status}, incident within {policy.start_date:%d-%b-%Y} – {policy.end_date:%d-%b-%Y}"

    # R02 waiting periods
    if "no_diagnosis" in flags:
        notes["R02"] = "NA: diagnosis missing"
    else:
        cover_from = max(policy.inception_date, member.enrolled_date)
        days = (claim.incident_date - cover_from).days
        accident = claim.diagnosis_code.upper().startswith(ACCIDENT_ICD_PREFIXES)
        if days < policy.initial_waiting_days and not accident:
            notes["R02"] = f"{days} days since cover start, initial waiting {policy.initial_waiting_days} days"
            return Deduction(f"R02:{claim.claim_id}", "R02", WAITING_PERIOD, "Sec 6.1 (Initial waiting period)",
                             "Within initial waiting period", billed,
                             f"Cover started {cover_from:%d-%b-%Y}; incident {days} days later "
                             f"(< {policy.initial_waiting_days}-day initial waiting).",
                             all_lines, evidence={"cover_from": str(cover_from), "days_covered": days,
                                                  "waiting_days": policy.initial_waiting_days})
        for r in rules:
            if r.rule_type == "DISEASE_WAITING" and claim.diagnosis_code.upper().startswith(r.target.upper()):
                if days < (r.value or 0):
                    notes["R02"] = f"{r.description}: {days} of {int(r.value)} days served"
                    return Deduction(f"{r.rule_id}:{claim.claim_id}", r.rule_id, WAITING_PERIOD, r.clause_ref,
                                     f"Within waiting period for {r.target}", billed,
                                     f"{r.description}. Cover since {cover_from:%d-%b-%Y} = {days} days "
                                     f"(< {int(r.value)} required).",
                                     all_lines, evidence={"rule": r.description, "days_covered": days,
                                                          "required_days": r.value,
                                                          "diagnosis": claim.diagnosis_code})
        notes["R02"] = f"{days} days of continuous cover; no waiting period applies" + (" (accident)" if accident else "")

    # R03 diagnosis exclusions
    if "no_diagnosis" not in flags:
        for r in rules:
            if r.rule_type == "EXCLUSION_DIAGNOSIS" and claim.diagnosis_code.upper().startswith(r.target.upper()):
                notes["R03"] = f"Diagnosis {claim.diagnosis_code} excluded"
                return Deduction(f"{r.rule_id}:{claim.claim_id}", r.rule_id, EXCLUDED_DIAGNOSIS, r.clause_ref,
                                 f"Excluded diagnosis {claim.diagnosis_code}", billed,
                                 f"{r.description} (diagnosis {claim.diagnosis_code}).",
                                 all_lines, evidence={"rule": r.description, "diagnosis": claim.diagnosis_code})
    return None


def line_deductions(claim: Claim, ctx: Context, policy: Policy, lines: list[InvoiceLine],
                    remaining: dict[str, float], notes: dict[str, str]) -> list[Deduction]:
    """R03 (items), R04, R05, R06, R10. Mutates `remaining` (admissible amount left per line)."""
    out: list[Deduction] = []
    rules = ctx.rules_by_plan.get(policy.plan, [])

    def take(line: InvoiceLine, amount: float) -> float:
        amt = round(min(amount, remaining[line.line_id]), 2)
        remaining[line.line_id] = round(remaining[line.line_id] - amt, 2)
        return amt

    # R10 arithmetic (runs on the raw line; must precede everything else)
    for ln in lines:
        computed = round(ln.quantity * ln.unit_price, 2)
        if ln.amount - computed > 1.0:
            amt = take(ln, ln.amount - computed)
            out.append(Deduction(f"R10:{ln.line_id}", "R10", ARITHMETIC_ERROR, "Invoice validation",
                                 f"Line amount exceeds qty × price: {ln.description}", amt,
                                 f"{ln.quantity:g} × {inr(ln.unit_price)} = {inr(computed)}, but line billed "
                                 f"{inr(ln.amount)} → excess {inr(amt)}.",
                                 [ln.line_id], evidence={"line": ln.line_id, "computed": computed, "billed": ln.amount}))
    notes.setdefault("R10", "All lines reconcile to quantity × unit price")

    # R03 excluded items
    for ln in lines:
        for r in rules:
            if r.rule_type == "EXCLUSION_CODE" and _matches(r, ln) and remaining[ln.line_id] > 0:
                amt = take(ln, remaining[ln.line_id])
                out.append(Deduction(f"{r.rule_id}:{ln.line_id}", r.rule_id, EXCLUDED_SERVICE, r.clause_ref,
                                     f"Excluded item: {ln.description}", amt,
                                     f"{ln.description} ({ln.service_code}) is non-payable under {r.clause_ref}: "
                                     f"{ln.quantity:g} × {inr(ln.unit_price)} = {inr(amt)} excluded.",
                                     [ln.line_id], evidence={"rule": r.description, "service_code": ln.service_code}))
                break
    notes.setdefault("R03", "No excluded diagnosis or non-payable items billed")

    # R04 / R05 daily limits
    for cat, limit, rid, category, label in (
        ("room", policy.room_rent_limit, "R04", ROOM_RENT_LIMIT, "Room rent"),
        ("icu", policy.icu_limit, "R05", ICU_LIMIT, "ICU"),
    ):
        notes.setdefault(rid, f"{label} within {inr(limit)}/day" if any(l.category == cat for l in lines)
                         else f"NA: no {label.lower()} charges")
        for ln in lines:
            if ln.category == cat and ln.unit_price > limit + 0.5 and remaining[ln.line_id] > 0:
                excess = (ln.unit_price - limit) * ln.quantity
                amt = take(ln, excess)
                out.append(Deduction(f"{rid}:{ln.line_id}", rid, category, "Sec 5.1 (Room & ICU limits)",
                                     f"{label} above daily limit", amt,
                                     f"{label} {inr(ln.unit_price)}/day vs limit {inr(limit)}/day → "
                                     f"({inr(ln.unit_price)} − {inr(limit)}) × {ln.quantity:g} days = {inr(amt)}.",
                                     [ln.line_id], evidence={"rate": ln.unit_price, "limit": limit, "days": ln.quantity}))

    # R06 per-claim sub-limits
    for r in rules:
        if r.rule_type != "SUBLIMIT_PER_CLAIM":
            continue
        hit = [ln for ln in lines if _matches(r, ln) and remaining[ln.line_id] > 0]
        total = round(sum(remaining[ln.line_id] for ln in hit), 2)
        if hit and total > (r.value or 0) + 0.5:
            excess = round(total - r.value, 2)
            left = excess
            for ln in hit:
                left -= take(ln, left)
            out.append(Deduction(f"{r.rule_id}:{claim.claim_id}", r.rule_id, SUB_LIMIT, r.clause_ref,
                                 f"Sub-limit exceeded: {r.description}", excess,
                                 f"{r.description}: admissible billed {inr(total)} vs sub-limit {inr(r.value)} → "
                                 f"excess {inr(excess)}.",
                                 [ln.line_id for ln in hit], evidence={"rule": r.description, "billed": total,
                                                                       "sub_limit": r.value}))

    # R06 frequency caps (uses earlier claims of the member in the same policy year)
    for r in rules:
        if r.rule_type != "FREQUENCY_CAP":
            continue
        hit = sorted((ln for ln in lines if _matches(r, ln)), key=lambda l: (l.service_date, l.line_id))
        if not hit:
            continue
        prior = 0.0
        prior_claims = []
        for other in ctx.claims_by_member.get(claim.member_id, []):
            if not ctx.is_earlier(other, claim) or not (policy.start_date <= other.incident_date <= policy.end_date):
                continue
            n = sum(l.quantity for l in ctx.lines_by_claim.get(other.claim_id, []) if _matches(r, l))
            if n:
                prior += n
                prior_claims.append(other.claim_id)
        current = sum(l.quantity for l in hit)
        cap = r.value or 0
        excess_units = max(0.0, min(current, prior + current - cap))
        if excess_units > 0:
            amt, units_left = 0.0, excess_units
            for ln in reversed(hit):  # the latest sessions are the ones over the cap
                u = min(units_left, ln.quantity)
                amt += take(ln, u * ln.unit_price)
                units_left -= u
                if units_left <= 0:
                    break
            amt = round(amt, 2)
            out.append(Deduction(f"{r.rule_id}:{claim.claim_id}", r.rule_id, FREQUENCY_CAP, r.clause_ref,
                                 f"Frequency cap exceeded: {r.description}", amt,
                                 f"{r.description}. Already used {prior:g} in policy year + {current:g} in this claim "
                                 f"= {prior + current:g} > cap {cap:g} → {excess_units:g} excess sessions = {inr(amt)}.",
                                 [l.line_id for l in hit], evidence={"rule": r.description, "prior_units": prior,
                                                                     "current_units": current, "cap": cap,
                                                                     "prior_claims": prior_claims}))
    notes.setdefault("R06", "Within all sub-limits and frequency caps")
    return out


def group_by(items, key):
    d = defaultdict(list)
    for it in items:
        d[key(it)].append(it)
    return d
