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
    created_at: datetime = Field(default_factory=_now)


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
