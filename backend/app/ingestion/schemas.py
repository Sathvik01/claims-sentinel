"""Row-level validation models for every ingestible dataset."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, StringConstraints, model_validator

DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def _parse_date(v):
    if v is None or isinstance(v, date):
        return v.date() if isinstance(v, datetime) else v
    s = str(v).strip()
    if not s:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised or impossible date '{s}' (use YYYY-MM-DD or DD-MM-YYYY)")


def _parse_bool(v):
    if isinstance(v, bool) or v is None:
        return v
    return str(v).strip().lower() in ("true", "1", "yes", "y")


FlexDate = Annotated[date, BeforeValidator(_parse_date)]
OptDate = Annotated[date | None, BeforeValidator(_parse_date)]
Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9\-/._]*$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Money = Annotated[float, Field(ge=0)]
Bool = Annotated[bool, BeforeValidator(_parse_bool)]

CLAIM_TYPES = Literal["INPATIENT", "DAYCARE", "OUTPATIENT", "PHYSIO", "DIALYSIS"]
CATEGORIES = Literal["room", "icu", "consultation", "lab", "radiology", "procedure", "pharmacy", "consumables",
                     "physio", "dialysis", "chemo", "ambulance", "other"]


def _not_future(d: date | None, name: str):
    if d and d > date.today():
        raise ValueError(f"{name} {d} is in the future")


class PolicyRow(BaseModel):
    policy_no: Code
    holder_name: Text
    plan: Code
    sum_insured: Annotated[float, Field(gt=0)]
    inception_date: FlexDate
    start_date: FlexDate
    end_date: FlexDate
    status: Literal["ACTIVE", "LAPSED", "CANCELLED"]
    deductible: Money = 0
    copay_pct: Annotated[float, Field(ge=0, le=100)] = 0
    room_rent_limit: Money
    icu_limit: Money
    initial_waiting_days: Annotated[int, Field(ge=0)] = 30

    @model_validator(mode="after")
    def _dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        if self.inception_date > self.start_date:
            raise ValueError("inception_date is after start_date")
        return self


class PolicyRuleRow(BaseModel):
    rule_id: Code
    plan: Code
    clause_ref: Text
    rule_type: Literal["EXCLUSION_CODE", "EXCLUSION_DIAGNOSIS", "SUBLIMIT_PER_CLAIM", "FREQUENCY_CAP", "DISEASE_WAITING"]
    target_type: Literal["code", "category", "icd_prefix"]
    target: Text
    value: Annotated[float | None, Field(ge=0)] = None
    description: Text

    @model_validator(mode="after")
    def _value(self):
        if self.rule_type in ("SUBLIMIT_PER_CLAIM", "FREQUENCY_CAP", "DISEASE_WAITING") and self.value is None:
            raise ValueError(f"{self.rule_type} requires a numeric value")
        return self


class MemberRow(BaseModel):
    member_id: Code
    policy_no: Code
    name: Text
    relationship: Literal["SELF", "SPOUSE", "CHILD", "PARENT"]
    gender: Literal["M", "F", "O"]
    dob: FlexDate
    enrolled_date: FlexDate
    phone: str | None = None
    address: str | None = None


class ProviderRow(BaseModel):
    provider_id: Code
    name: Text
    provider_type: Literal["HOSPITAL", "DIAGNOSTIC", "CLINIC"]
    city: Text
    tier: Annotated[int, Field(ge=1, le=3)]
    network: Bool = True


class ClaimRow(BaseModel):
    claim_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^CLM-[A-Z0-9][A-Z0-9-]*$")]
    policy_no: Code
    member_id: Code
    claimant_name: Text
    claim_type: CLAIM_TYPES
    incident_date: FlexDate
    admission_date: OptDate = None
    discharge_date: OptDate = None
    diagnosis_code: str | None = None
    provider_id: Code
    invoice_no: str | None = None
    requested_amount: Money
    proposed_settlement: Money | None = None
    submitted_date: FlexDate
    status: Literal["PENDING", "PAID"] = "PENDING"
    payout_account: str | None = None

    @model_validator(mode="after")
    def _dates(self):
        for name in ("incident_date", "admission_date", "discharge_date", "submitted_date"):
            _not_future(getattr(self, name), name)
        if self.admission_date and self.discharge_date and self.discharge_date < self.admission_date:
            raise ValueError("discharge_date is before admission_date")
        if self.submitted_date < self.incident_date:
            raise ValueError("submitted_date is before incident_date")
        return self


class InvoiceLineRow(BaseModel):
    line_id: Code
    claim_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^CLM-[A-Z0-9][A-Z0-9-]*$")]
    invoice_no: str | None = None
    provider_id: Code
    service_date: FlexDate
    service_code: Code
    description: Text
    category: CATEGORIES
    quantity: Annotated[float, Field(gt=0)]
    unit_price: Money
    amount: Money

    @model_validator(mode="after")
    def _future(self):
        _not_future(self.service_date, "service_date")
        return self


class SettlementRow(BaseModel):
    settlement_id: Code
    claim_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^CLM-[A-Z0-9][A-Z0-9-]*$")]
    approved_amount: Money
    paid_amount: Money
    paid_date: FlexDate
    payment_ref: str | None = None
