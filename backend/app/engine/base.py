"""Shared engine types and the in-memory context the detectors run against."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlmodel import Session, select

from app.models import Claim, InvoiceLine, Member, Policy, PolicyRule, Provider, Settlement

# Finding types / amount bases
CONFIRMED = "CONFIRMED_VIOLATION"
SUSPECTED = "SUSPECTED_PATTERN"
NEEDS_INFO = "NEEDS_INFO"
CALCULATED = "CALCULATED"
ESTIMATED = "ESTIMATED"
NOT_DETERMINABLE = "NOT_DETERMINABLE"

# Categories
DUPLICATE_CLAIM = "DUPLICATE_CLAIM"
DUPLICATE_LINE = "DUPLICATE_LINE"
NEAR_DUPLICATE = "NEAR_DUPLICATE"
COVERAGE = "COVERAGE"
WAITING_PERIOD = "WAITING_PERIOD"
EXCLUDED_SERVICE = "EXCLUDED_SERVICE"
EXCLUDED_DIAGNOSIS = "EXCLUDED_DIAGNOSIS"
ROOM_RENT_LIMIT = "ROOM_RENT_LIMIT"
ICU_LIMIT = "ICU_LIMIT"
SUB_LIMIT = "SUB_LIMIT"
FREQUENCY_CAP = "FREQUENCY_CAP"
ARITHMETIC_ERROR = "ARITHMETIC_ERROR"
SETTLEMENT_MISCALC = "SETTLEMENT_MISCALC"
PROVIDER_OUTLIER = "PROVIDER_OUTLIER"
INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"
DUPLICATE_DOCUMENT = "DUPLICATE_DOCUMENT"
DOCUMENT_TAMPER = "DOCUMENT_TAMPER"
RELATED_CLAIMS = "RELATED_CLAIMS"
DOCUMENT_MISMATCH = "DOCUMENT_MISMATCH"

CATEGORY_LABELS = {
    DUPLICATE_CLAIM: "Duplicate claim",
    DUPLICATE_LINE: "Duplicate line item",
    NEAR_DUPLICATE: "Near-duplicate claim",
    COVERAGE: "Coverage status",
    WAITING_PERIOD: "Waiting period",
    EXCLUDED_SERVICE: "Excluded service",
    EXCLUDED_DIAGNOSIS: "Excluded diagnosis",
    ROOM_RENT_LIMIT: "Room rent limit",
    ICU_LIMIT: "ICU limit",
    SUB_LIMIT: "Sub-limit breach",
    FREQUENCY_CAP: "Frequency cap",
    ARITHMETIC_ERROR: "Invoice arithmetic",
    SETTLEMENT_MISCALC: "Settlement calculation",
    PROVIDER_OUTLIER: "Unusual provider billing",
    INCOMPLETE_EVIDENCE: "Incomplete evidence",
    DUPLICATE_DOCUMENT: "Reused document image",
    DOCUMENT_TAMPER: "Document tampering",
    RELATED_CLAIMS: "Related claims",
    DOCUMENT_MISMATCH: "Claim vs document mismatch",
}

# Services that legitimately recur on separate dates within/across claims.
REPEATABLE_CATEGORIES = {"room", "icu", "physio", "dialysis", "chemo", "consultation", "pharmacy", "lab"}
ACCIDENT_ICD_PREFIXES = ("S", "T", "V", "W", "X", "Y")


def inr(x: float | None) -> str:
    if x is None:
        return "—"
    neg = x < 0
    x = abs(round(x, 2))
    whole, frac = divmod(round(x * 100), 100)
    s = str(int(whole))
    if len(s) > 3:  # Indian digit grouping: 12,34,567
        head, tail = s[:-3], s[-3:]
        head = re.sub(r"(\d)(?=(\d{2})+$)", r"\1,", head)
        s = f"{head},{tail}"
    out = f"₹{s}" + (f".{frac:02d}" if frac else "")
    return f"−{out}" if neg else out


def norm_invoice(ref: str | None) -> str | None:
    if not ref:
        return None
    return re.sub(r"[^A-Z0-9]", "", ref.upper()) or None


def norm_invoice_loose(ref: str | None) -> str:
    """Drops separators and leading zeros of digit groups: INV-0045 ≈ INV-045."""
    if not ref:
        return ""
    groups = re.findall(r"[A-Za-z]+|\d+", ref.upper())
    return "".join(str(int(g)) if g.isdigit() else g for g in groups)


@dataclass
class Deduction:
    """An amount the policy says must not be paid. Feeds the settlement waterfall."""

    key: str
    rule_id: str
    category: str
    clause_ref: str | None
    title: str
    amount: float  # billed excess removed from the admissible amount
    calculation: str
    line_ids: list[str] = field(default_factory=list)
    related_claim_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class FindingDraft:
    category: str
    rule_id: str
    title: str
    finding_type: str
    amount_basis: str
    calculation: str
    clause_ref: str | None = None
    billed_excess: float | None = None
    leakage_amount: float | None = None
    line_ids: list[str] = field(default_factory=list)
    related_claim_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass
class Check:
    rule_id: str
    name: str
    outcome: str  # PASS | FAIL | DEDUCTED | NA
    detail: str
    clause_ref: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class Context:
    """Everything the detectors need, loaded once per analysis run."""

    def __init__(
        self,
        policies: list[Policy],
        rules: list[PolicyRule],
        members: list[Member],
        providers: list[Provider],
        claims: list[Claim],
        lines: list[InvoiceLine],
        settlements: list[Settlement],
    ):
        self.policies = {p.policy_no: p for p in policies}
        self.rules_by_plan: dict[str, list[PolicyRule]] = defaultdict(list)
        for r in rules:
            self.rules_by_plan[r.plan].append(r)
        self.members = {m.member_id: m for m in members}
        self.providers = {p.provider_id: p for p in providers}
        self.claims = {c.claim_id: c for c in claims}
        self.lines_by_claim: dict[str, list[InvoiceLine]] = defaultdict(list)
        for ln in lines:
            self.lines_by_claim[ln.claim_id].append(ln)
        self.settlements_by_claim: dict[str, list[Settlement]] = defaultdict(list)
        for s in settlements:
            self.settlements_by_claim[s.claim_id].append(s)
        self.claims_by_member: dict[str, list[Claim]] = defaultdict(list)
        self.claims_by_policy: dict[str, list[Claim]] = defaultdict(list)
        for c in claims:
            self.claims_by_member[c.member_id].append(c)
            self.claims_by_policy[c.policy_no].append(c)

    @classmethod
    def load(cls, session: Session) -> Context:
        return cls(
            list(session.exec(select(Policy))),
            list(session.exec(select(PolicyRule))),
            list(session.exec(select(Member))),
            list(session.exec(select(Provider))),
            list(session.exec(select(Claim))),
            list(session.exec(select(InvoiceLine))),
            list(session.exec(select(Settlement))),
        )

    def pending_claims(self) -> list[Claim]:
        return sorted((c for c in self.claims.values() if c.status == "PENDING"), key=lambda c: c.claim_id)

    def is_earlier(self, other: Claim, claim: Claim) -> bool:
        """True if `other` was on file before `claim` (paid history always counts as earlier)."""
        if other.claim_id == claim.claim_id:
            return False
        if other.status == "PAID" and claim.status != "PAID":
            return True
        if other.status != claim.status:
            return False
        return (other.submitted_date, other.claim_id) < (claim.submitted_date, claim.claim_id)

    def paid_amount(self, claim_id: str) -> float:
        return sum(s.paid_amount for s in self.settlements_by_claim.get(claim_id, []))

    def policy_year_paid(self, policy: Policy, before: date | None = None) -> float:
        total = 0.0
        for c in self.claims_by_policy.get(policy.policy_no, []):
            if c.status != "PAID" or not (policy.start_date <= c.incident_date <= policy.end_date):
                continue
            if before and c.incident_date > before:
                continue
            total += self.paid_amount(c.claim_id)
        return total
