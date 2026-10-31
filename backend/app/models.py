"""Database tables. Reference/claims data mirrors the ingestion datasets; the rest is engine output."""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- reference data
class Policy(SQLModel, table=True):
    policy_no: str = Field(primary_key=True)
    holder_name: str
    plan: str = Field(index=True)
    sum_insured: float
    inception_date: date  # start of continuous cover (drives waiting periods)
    start_date: date  # current policy year
    end_date: date
    status: str  # ACTIVE | LAPSED | CANCELLED
    deductible: float = 0
    copay_pct: float = 0
    room_rent_limit: float
    icu_limit: float
    initial_waiting_days: int = 30


class PolicyRule(SQLModel, table=True):
    rule_id: str = Field(primary_key=True)
    plan: str = Field(index=True)
    clause_ref: str
    rule_type: str  # EXCLUSION_CODE | EXCLUSION_DIAGNOSIS | SUBLIMIT_PER_CLAIM | FREQUENCY_CAP | DISEASE_WAITING
    target_type: str  # code | category | icd_prefix
    target: str
    value: float | None = None
    description: str


class Member(SQLModel, table=True):
    member_id: str = Field(primary_key=True)
    policy_no: str = Field(index=True)
    name: str
    relationship: str
    gender: str
    dob: date
    enrolled_date: date
    phone: str | None = None
    address: str | None = None


class Provider(SQLModel, table=True):
    provider_id: str = Field(primary_key=True)
    name: str
    provider_type: str  # HOSPITAL | DIAGNOSTIC | CLINIC
    city: str
    tier: int
    network: bool = True


# ---------------------------------------------------------------- claims data
class Claim(SQLModel, table=True):
    claim_id: str = Field(primary_key=True)
    policy_no: str = Field(index=True)
    member_id: str = Field(index=True)
    claimant_name: str
    claim_type: str  # INPATIENT | DAYCARE | OUTPATIENT | PHYSIO | DIALYSIS
    incident_date: date
    admission_date: date | None = None
    discharge_date: date | None = None
    diagnosis_code: str | None = None
    provider_id: str = Field(index=True)
    invoice_no: str | None = None
    requested_amount: float
    proposed_settlement: float | None = None
    submitted_date: date
    status: str = "PENDING"  # PENDING (to be assessed) | PAID (history)
    source: str = "upload"
    payout_account: str | None = None  # masked bank account the settlement is paid to
    state: str = "SUBMITTED"  # lifecycle state, see app/workflow/lifecycle.py
    assigned_to: str | None = Field(default=None, index=True)  # username


class InvoiceLine(SQLModel, table=True):
    line_id: str = Field(primary_key=True)
    claim_id: str = Field(index=True)
    invoice_no: str | None = None
    provider_id: str
    service_date: date
    service_code: str
    description: str
    category: str
    quantity: float
    unit_price: float
    amount: float


class Settlement(SQLModel, table=True):
    settlement_id: str = Field(primary_key=True)
    claim_id: str = Field(index=True)
    approved_amount: float
    paid_amount: float
    paid_date: date
    payment_ref: str | None = None


# ---------------------------------------------------------------- ingestion
class IngestionBatch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    dataset: str
    filename: str
    file_format: str
    created_at: datetime = Field(default_factory=_now)
    received: int = 0
    accepted: int = 0
    rejected: int = 0
    warnings: int = 0


class IngestionError(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    batch_id: int = Field(index=True)
    row_number: int | None = None
    record_id: str | None = None
    field: str | None = None
    value: str | None = None
    message: str
    severity: str  # ERROR (row rejected) | WARNING (row accepted, flagged)


class Document(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    filename: str
    content_type: str
    path: str
    status: str = "UPLOADED"  # UPLOADED | EXTRACTED | FAILED | CONFIRMED
    extraction: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None
    claim_id: str | None = None
    doc_type: str = "INVOICE"  # INVOICE | DISCHARGE_SUMMARY | PRESCRIPTION | REPORT | OTHER
    source: str = "upload"  # upload | portal | synthetic
    uploaded_by: str | None = None
    created_at: datetime = Field(default_factory=_now)


class ExtractedField(SQLModel, table=True):
    """One value read from a document, with where it came from."""
    id: int | None = Field(default=None, primary_key=True)
    doc_id: int = Field(index=True)
    claim_id: str | None = Field(default=None, index=True)
    doc_type: str
    path: str  # e.g. invoice_no, admission_date, lines[3].amount
    value: str
    page: int | None = None
    source_text: str | None = None
    verified: bool = False  # the source text was found on the page
    method: str  # template | text-llm | vision-llm
    model: str | None = None


# ---------------------------------------------------------------- engine output
class AnalysisRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    claims_analyzed: int = 0
    findings_count: int = 0


class ClaimAssessment(SQLModel, table=True):
    claim_id: str = Field(primary_key=True)
    run_id: int
    billed_amount: float
    expected_settlement: float | None = None
    proposed_settlement: float | None = None
    gap: float | None = None
    reconciliation: str | None = None  # how the proposed amount was explained
    waterfall: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    checks: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    cleared: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    documents: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))  # claim vs document rows


class Finding(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    claim_id: str = Field(index=True)
    category: str = Field(index=True)
    rule_id: str
    clause_ref: str | None = None
    title: str
    finding_type: str  # CONFIRMED_VIOLATION | SUSPECTED_PATTERN | NEEDS_INFO
    amount_basis: str  # CALCULATED | ESTIMATED | NOT_DETERMINABLE
    billed_excess: float | None = None  # line-level discrepancy on the invoice
    leakage_amount: float | None = None  # impact on the amount payable
    calculation: str
    evidence: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    line_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    related_claim_id: str | None = None
    severity: str  # HIGH | MEDIUM | LOW
    confidence: float = 1.0
    status: str = "OPEN"  # OPEN | CONFIRMED | CORRECTED | DISMISSED
    corrected_amount: float | None = None
    ai_explanation: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class FindingAction(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    finding_id: int = Field(index=True)
    user: str
    role: str
    action: str  # CONFIRM | CORRECT | DISMISS | REOPEN
    reason: str | None = None
    old_amount: float | None = None
    new_amount: float | None = None
    created_at: datetime = Field(default_factory=_now)


# ---------------------------------------------------------------- document integrity
class DocumentScan(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    doc_id: int = Field(index=True)
    status: str = "QUEUED"  # QUEUED | RUNNING | DONE | FAILED
    mode: str | None = None  # agent | fixed pipeline
    steps: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    results: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))  # raw tool outputs by skill
    score: float | None = None
    level: str | None = None  # LOW | MEDIUM | HIGH
    contributions: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    report: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None


class ImageFingerprint(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    doc_id: int | None = Field(default=None, index=True)
    claim_id: str | None = Field(default=None, index=True)
    page: int = 0
    phash: str
    dhash: str
    image_path: str
    label: str
    source: str  # upload | seed


# ---------------------------------------------------------------- claim-level review
class ClaimDecision(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    claim_id: str = Field(index=True)
    decision: str  # APPROVED | PARTIALLY_APPROVED | REJECTED | ON_HOLD (legacy: ADJUSTED)
    approved_amount: float | None = None
    reason: str
    user: str
    role: str
    status: str = "FINAL"  # FINAL | PENDING_APPROVAL | DECLINED
    approved_by: str | None = None
    approval_reason: str | None = None
    created_at: datetime = Field(default_factory=_now)


class ClaimLink(SQLModel, table=True):
    """Cross-claim correlation links from the latest analysis run."""
    id: int | None = Field(default=None, primary_key=True)
    claim_a: str = Field(index=True)
    claim_b: str = Field(index=True)
    rule: str
    strength: str  # STRONG | MEDIUM | WEAK
    attribute: str
    value: str
    explanation: str


# ---------------------------------------------------------------- identity, workflow, audit
class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    full_name: str
    role: str  # POLICYHOLDER | HANDLER | ANALYST | SENIOR | SIU | ADMIN
    password_hash: str
    member_id: str | None = None  # policyholders only
    policy_no: str | None = None
    active: bool = True


class AuditEvent(SQLModel, table=True):
    """Append-only. `hash` chains every event to the previous one (sha256), so edits are detectable."""
    id: int | None = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=_now, index=True)
    claim_id: str | None = Field(default=None, index=True)
    entity: str  # claim | finding | recommendation | document | evidence_request | escalation | note | analysis | user
    entity_id: str | None = None
    action: str
    actor: str  # username or system:<component>
    actor_role: str
    reason: str | None = None
    before: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    after: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    ai_generated: bool = False
    model: str | None = None
    prev_hash: str
    hash: str = Field(index=True)


class Notification(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str | None = Field(default=None, index=True)  # direct recipient
    role: str | None = Field(default=None, index=True)  # or everyone in a role
    claim_id: str | None = None
    kind: str
    message: str
    read: bool = False
    created_at: datetime = Field(default_factory=_now)


class Recommendation(SQLModel, table=True):
    """Immutable: a recalculation that changes the numbers adds a new version and supersedes the old row."""
    id: int | None = Field(default=None, primary_key=True)
    claim_id: str = Field(index=True)
    finding_id: int = Field(index=True)
    version: int = 1
    superseded: bool = False
    type: str  # REMOVE_LINE | CAP_DAILY_RATE | APPLY_COPAY | REJECT_CLAIM | ...
    action: str  # one-line instruction
    target_line_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    original_amount: float
    corrected_amount: float
    delta: float
    settlement_impact: float | None = None  # reduction of the amount payable
    calculation: str
    rule_id: str
    clause_ref: str | None = None
    clause_text: str | None = None
    generated_by: str = "engine"
    run_id: int | None = None
    created_at: datetime = Field(default_factory=_now)



# ---------------------------------------------------------------- investigation workflow
class FindingDecision(SQLModel, table=True):
    """Analyst decision on one finding. The recommendation it refers to is never modified."""
    id: int | None = Field(default=None, primary_key=True)
    finding_id: int = Field(index=True)
    claim_id: str = Field(index=True)
    decision: str  # CONFIRM_DISCREPANCY | FALSE_POSITIVE | REQUEST_EVIDENCE | ESCALATE | ACCEPT_CORRECTION
    reason: str | None = None
    recommendation_id: int | None = None
    recommended_impact: float | None = None
    override_impact: float | None = None  # analyst's figure when overriding the recommendation
    status: str = "EFFECTIVE"  # EFFECTIVE | PENDING_APPROVAL | APPROVED | DECLINED
    user: str
    role: str
    approved_by: str | None = None
    approval_reason: str | None = None
    created_at: datetime = Field(default_factory=_now)


class EvidenceRequest(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    claim_id: str = Field(index=True)
    finding_id: int | None = None
    audience: str = "POLICYHOLDER"  # POLICYHOLDER | PROVIDER
    message: str
    doc_types: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    due_date: date
    status: str = "OPEN"  # OPEN | RESPONDED | CLOSED
    requested_by: str
    created_at: datetime = Field(default_factory=_now)


class EvidenceResponse(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    request_id: int = Field(index=True)
    claim_id: str = Field(index=True)
    responder: str
    message: str | None = None
    doc_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now)


class Escalation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    claim_id: str = Field(index=True)
    finding_id: int | None = None
    target: str  # SENIOR | SIU
    reason: str
    raised_by: str
    status: str = "OPEN"  # OPEN | RESOLVED
    outcome: str | None = None  # SIU: SUBSTANTIATED | NOT_SUBSTANTIATED; senior: free text
    resolution: str | None = None
    resolved_by: str | None = None
    created_at: datetime = Field(default_factory=_now)
    resolved_at: datetime | None = None


class Note(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    claim_id: str = Field(index=True)
    author: str
    role: str
    visibility: str = "INTERNAL"  # INTERNAL | SIU
    text: str
    created_at: datetime = Field(default_factory=_now)
