"""Plain-language explanation of a finding. The LLM may only restate the engine's evidence: any figure
in its answer that is not in the evidence rejects the answer and the deterministic template is used."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field

from app.ai import llm
from app.engine.base import CATEGORY_LABELS, NEEDS_INFO, SUSPECTED, inr
from app.models import Claim, Finding


class Explanation(BaseModel):
    summary: str = Field(description="One sentence a claims manager can read at a glance")
    why_flagged: str = Field(description="2-4 sentences: the rule or pattern, the evidence, and the arithmetic")
    what_to_check: list[str] = Field(description="2-4 concrete verification steps for the reviewer")
    clarification_request: str | None = Field(None, description="Only for missing information: a short polite "
                                                                "request to the hospital/claimant")


SYSTEM = (
    "You explain health-insurance claim audit findings to claims reviewers. Use ONLY facts and numbers present "
    "in the finding JSON. Do not compute new figures (not even differences or totals), do not round differently, "
    "and use each figure only with the meaning the JSON gives it (e.g. billed is not admissible). "
    "Do not speculate about fraud. "
    "Write INR amounts with the ₹ symbol and Indian digit grouping exactly as given. Suspected patterns must be "
    "described as needing review, not as confirmed. Be concise."
)

CHECKS = {
    "DUPLICATE_CLAIM": ["Compare both invoices and admission records side by side",
                        "Confirm with the provider whether a single episode was billed twice"],
    "DUPLICATE_LINE": ["Check the itemised bill and medical record for the repeated service",
                       "Confirm whether the service was actually performed twice on that date"],
    "NEAR_DUPLICATE": ["Compare admission/discharge records and the treating doctor for both claims",
                       "Ask the provider to confirm whether these are separate episodes"],
    "ROOM_RENT_LIMIT": ["Verify the room category occupied against the policy schedule",
                        "Apply the per-day limit (and any proportionate deduction clause)"],
    "ICU_LIMIT": ["Verify ICU days against the clinical notes", "Apply the policy's ICU per-day limit"],
    "EXCLUDED_SERVICE": ["Confirm the items are in the non-payable list", "Deduct them from the admissible amount"],
    "EXCLUDED_DIAGNOSIS": ["Review the diagnosis and treatment intent in the discharge summary",
                           "Check whether any medically necessary component is payable"],
    "COVERAGE": ["Check the policy status and premium payment history on the incident date",
                 "Confirm the member is listed on the policy schedule"],
    "WAITING_PERIOD": ["Verify continuous-cover history including portability", "Check the waiting-period clause"],
    "SUB_LIMIT": ["Apply the sub-limit from the policy schedule", "Check whether a higher plan limit applies"],
    "FREQUENCY_CAP": ["Count sessions already paid in this policy year", "Pay only sessions within the cap"],
    "ARITHMETIC_ERROR": ["Recompute quantity × rate on the original bill", "Ask the provider for a corrected bill"],
    "SETTLEMENT_MISCALC": ["Recompute deductible, co-pay and sum-insured cap", "Correct the settlement before payment"],
    "PROVIDER_OUTLIER": ["Compare with the provider's agreed tariff", "Review whether a higher-cost variant was used"],
    "INCOMPLETE_EVIDENCE": ["Request the missing documents before assessing", "Re-run the analysis once received"],
    "DUPLICATE_DOCUMENT": ["Open both documents side by side and compare patient, dates and amounts",
                           "Ask the provider whether this bill was already billed under the matched claim"],
    "RELATED_CLAIMS": ["Open the linked claims and compare claimant, provider, invoice and bank details",
                       "Confirm the relationship with the provider or claimants; refer to SIU if unexplained"],
    "DOCUMENT_MISMATCH": ["Check the highlighted values against the source document page",
                          "Settle on the document value unless the provider issues a corrected bill"],
    "DOCUMENT_TAMPER": ["Inspect the highlighted regions on the original document",
                        "Request the original bill directly from the provider before settlement"],
}


def _evidence_json(f: Finding, claim: Claim | None) -> dict:
    return {
        "finding": {"category": CATEGORY_LABELS.get(f.category, f.category), "rule_id": f.rule_id,
                    "clause": f.clause_ref, "title": f.title, "type": f.finding_type, "amount_basis": f.amount_basis,
                    "billed_excess": inr(f.billed_excess) if f.billed_excess is not None else None,
                    "leakage_amount": inr(f.leakage_amount) if f.leakage_amount is not None else None,
                    "calculation": f.calculation, "related_claim": f.related_claim_id, "evidence": f.evidence},
        "claim": None if claim is None else {
            "claim_id": claim.claim_id, "claim_type": claim.claim_type, "diagnosis": claim.diagnosis_code,
            "incident_date": str(claim.incident_date), "requested": inr(claim.requested_amount),
            "proposed_settlement": inr(claim.proposed_settlement)},
    }


def template(f: Finding) -> dict:
    if f.finding_type == NEEDS_INFO:
        summary = f"Claim {f.claim_id} cannot be fully assessed: {f.title.split(': ', 1)[-1]}."
    elif f.finding_type == SUSPECTED:
        summary = f"{CATEGORY_LABELS.get(f.category, f.category)} pattern on {f.claim_id} needs review " \
                  f"(estimated {inr(f.leakage_amount)})."
    else:
        summary = f"{f.title} on {f.claim_id}: {inr(f.leakage_amount)} overpayment."
    return {"summary": summary, "why_flagged": f.calculation,
            "what_to_check": CHECKS.get(f.category, ["Review the supporting records"]),
            "clarification_request": (f.evidence or {}).get("clarification_request"), "source": "template"}


def _numbers(text: str) -> set[str]:
    out = set()
    for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        v = float(n.replace(",", ""))
        out.add(str(int(v)) if v.is_integer() else f"{v:.2f}")
    return out


def explain(f: Finding, claim: Claim | None) -> dict:
    if not llm.available():
        return template(f)
    payload = _evidence_json(f, claim)
    source_text = json.dumps(payload, ensure_ascii=False)
    prompt = json.dumps(payload, ensure_ascii=False, indent=1)
    unsupported: set[str] = set()
    for _ in range(2):  # one corrective retry if the answer introduces figures
        try:
            out = llm.generate(Explanation, prompt, SYSTEM)
        except Exception as e:  # network / quota / safety → deterministic fallback
            return template(f) | {"note": f"AI explanation unavailable: {type(e).__name__}"}
        text = " ".join([out.summary, out.why_flagged, *out.what_to_check, out.clarification_request or ""])
        unsupported = _numbers(text) - _numbers(source_text)
        if not unsupported:
            return out.model_dump() | {"source": "llm"}
        prompt += (f"\n\nYour previous answer used figures that are not in the JSON ({', '.join(sorted(unsupported))}). "
                   "Rewrite it using only figures that appear verbatim in the JSON.")
    return template(f) | {"note": f"AI answer rejected: figures not in evidence ({', '.join(sorted(unsupported)[:5])})"}
