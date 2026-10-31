"""File parsing, row validation, referential checks and loading. Nothing is dropped silently:
every rejected row and every warning is persisted against its ingestion batch."""

from __future__ import annotations

import io
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ValidationError
from sqlalchemy import insert
from sqlmodel import Session, SQLModel, select

from app.ingestion.schemas import (
    ClaimRow,
    InvoiceLineRow,
    MemberRow,
    PolicyRow,
    PolicyRuleRow,
    ProviderRow,
    SettlementRow,
)
from app.models import (
    Claim,
    IngestionBatch,
    IngestionError,
    InvoiceLine,
    Member,
    Policy,
    PolicyRule,
    Provider,
    Settlement,
)

# dataset → (row schema, table, primary key). Order = dependency order for bundles.
DATASETS: dict[str, tuple[type[BaseModel], type[SQLModel], str]] = {
    "providers": (ProviderRow, Provider, "provider_id"),
    "policies": (PolicyRow, Policy, "policy_no"),
    "policy_rules": (PolicyRuleRow, PolicyRule, "rule_id"),
    "members": (MemberRow, Member, "member_id"),
    "claims": (ClaimRow, Claim, "claim_id"),
    "invoice_lines": (InvoiceLineRow, InvoiceLine, "line_id"),
    "settlements": (SettlementRow, Settlement, "settlement_id"),
}


class IngestError(ValueError):
    pass


def _clean(v: Any) -> Any:
    if v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA:
        return None
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def read_file(content: bytes, filename: str) -> tuple[str, dict[str | None, list[dict]]]:
    """Returns (format, {sheet_or_key_or_None: records})."""
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".csv":
            df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
            return "csv", {None: df.to_dict("records")}
        if ext in (".xlsx", ".xls"):
            sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, dtype=str)
            return "xlsx", {name: df.to_dict("records") for name, df in sheets.items()}
        if ext == ".json":
            data = json.loads(content.decode("utf-8-sig"))
            if isinstance(data, list):
                return "json", {None: data}
            if isinstance(data, dict) and all(isinstance(v, list) for v in data.values()):
                return "json", dict(data)
            raise IngestError("JSON must be a list of records or an object of {dataset: [records]}")
    except IngestError:
        raise
    except Exception as e:  # malformed file → surface the parser message
        raise IngestError(f"Could not parse {filename}: {e}") from e
    raise IngestError(f"Unsupported file type '{ext}'. Use CSV, XLSX or JSON.")


class _Refs:
    def __init__(self, session: Session):
        self.policies = set(session.exec(select(Policy.policy_no)))
        self.member_policy = dict(session.exec(select(Member.member_id, Member.policy_no)).all())
        self.providers = set(session.exec(select(Provider.provider_id)))
        self.claims = set(session.exec(select(Claim.claim_id)))


def _ref_errors(dataset: str, row: BaseModel, refs: _Refs) -> list[tuple[str, str, str]]:
    errs = []
    if dataset == "members" and row.policy_no not in refs.policies:
        errs.append(("policy_no", row.policy_no, "unknown policy (load policies first)"))
    elif dataset == "claims":
        if row.policy_no not in refs.policies:
            errs.append(("policy_no", row.policy_no, "unknown policy"))
        mp = refs.member_policy.get(row.member_id)
        if mp is None:
            errs.append(("member_id", row.member_id, "unknown member"))
        elif mp != row.policy_no:
            errs.append(("member_id", row.member_id, f"member belongs to policy {mp}, not {row.policy_no}"))
        if row.provider_id not in refs.providers:
            errs.append(("provider_id", row.provider_id, "unknown provider"))
    elif dataset == "invoice_lines":
        if row.claim_id not in refs.claims:
            errs.append(("claim_id", row.claim_id, "unknown claim (load claims first)"))
        if row.provider_id not in refs.providers:
            errs.append(("provider_id", row.provider_id, "unknown provider"))
    elif dataset == "settlements" and row.claim_id not in refs.claims:
        errs.append(("claim_id", row.claim_id, "unknown claim"))
    return errs


def _warnings(dataset: str, row: BaseModel) -> list[tuple[str, str, str]]:
    w = []
    if dataset == "claims" and row.proposed_settlement is not None and row.proposed_settlement > row.requested_amount:
        w.append(("proposed_settlement", str(row.proposed_settlement),
                  f"proposed settlement exceeds requested amount {row.requested_amount}"))
    if dataset == "invoice_lines" and abs(row.amount - row.quantity * row.unit_price) > 1:
        w.append(("amount", str(row.amount), f"amount ≠ quantity × unit price ({row.quantity:g} × {row.unit_price}); "
                                             "accepted and checked by rule R10"))
    return w


def ingest_records(session: Session, dataset: str, records: list[dict], filename: str, fmt: str,
                   source: str | None = None) -> IngestionBatch:
    if dataset not in DATASETS:
        raise IngestError(f"Unknown dataset '{dataset}'. Expected one of {', '.join(DATASETS)}")
    schema, table, pk = DATASETS[dataset]
    batch = IngestionBatch(dataset=dataset, filename=filename, file_format=fmt, received=len(records))
    session.add(batch)
    session.flush()
    errors: list[IngestionError] = []

    def err(row_no, rid, field, value, msg, sev="ERROR"):
        errors.append(IngestionError(batch_id=batch.id, row_number=row_no, record_id=rid, field=field,
                                     value=None if value is None else str(value)[:200], message=msg, severity=sev))

    fields = set(schema.model_fields)
    if records:
        extra = sorted(set().union(*(r.keys() for r in records)) - fields)
        if extra:
            err(None, None, ", ".join(map(str, extra)), None, "column(s) not part of the schema were ignored", "WARNING")
        missing = sorted(f for f, info in schema.model_fields.items()
                         if info.is_required() and f not in set().union(*(r.keys() for r in records)))
        if missing:
            err(None, None, ", ".join(missing), None, "required column(s) missing from file")

    refs = _Refs(session)
    existing = set(session.exec(select(getattr(table, pk))))
    seen: set[str] = set()
    accepted: list[dict] = []
    for i, raw in enumerate(records, start=1):
        data = {k: _clean(v) for k, v in raw.items() if k in fields}
        rid = data.get(pk)
        try:
            row = schema.model_validate(data)
        except ValidationError as e:
            for d in e.errors():
                loc = ".".join(str(x) for x in d["loc"]) or None
                err(i, rid, loc, d.get("input") if loc else None, d["msg"].removeprefix("Value error, "))
            continue
        rid = getattr(row, pk)
        if rid in seen:
            err(i, rid, pk, rid, "duplicate id within this file")
            continue
        if rid in existing:
            err(i, rid, pk, rid, "id already loaded")
            continue
        ref = _ref_errors(dataset, row, refs)
        for f, v, m in ref:
            err(i, rid, f, v, m)
        if ref:
            continue
        for f, v, m in _warnings(dataset, row):
            err(i, rid, f, v, m, "WARNING")
        seen.add(rid)
        rec = row.model_dump()
        if dataset == "claims" and source:
            rec["source"] = source
        accepted.append(rec)

    if accepted:
        session.execute(insert(table), accepted)
    session.add_all(errors)
    batch.accepted = len(accepted)
    batch.rejected = batch.received - batch.accepted
    batch.warnings = sum(1 for e in errors if e.severity == "WARNING")
    session.commit()
    session.refresh(batch)
    return batch


def ingest_file(session: Session, dataset: str | None, content: bytes, filename: str) -> list[IngestionBatch]:
    """`dataset=None` (or 'bundle') loads a multi-sheet workbook / multi-key JSON in dependency order."""
    fmt, parts = read_file(content, filename)
    if dataset and dataset != "bundle":
        if None in parts:
            return [ingest_records(session, dataset, parts[None], filename, fmt)]
        if dataset in parts:
            return [ingest_records(session, dataset, parts[dataset], filename, fmt)]
        if len(parts) == 1:
            return [ingest_records(session, dataset, next(iter(parts.values())), filename, fmt)]
        raise IngestError(f"No sheet named '{dataset}' in {filename}")
    unknown = [k for k in parts if k not in DATASETS]
    if None in parts or not set(parts) & set(DATASETS):
        raise IngestError("Bundle files need one sheet / JSON key per dataset: " + ", ".join(DATASETS))
    batches = [ingest_records(session, name, parts[name], f"{filename}#{name}", fmt)
               for name in DATASETS if name in parts]
    if unknown:
        b = batches[0]
        session.add(IngestionError(batch_id=b.id, message=f"sheets ignored (not a dataset): {', '.join(map(str, unknown))}",
                                   severity="WARNING"))
        b.warnings += 1
        session.commit()
    return batches


def load_sample_dir(session: Session, directory: Path) -> list[IngestionBatch]:
    batches = []
    for name in DATASETS:
        path = directory / f"{name}.csv"
        if path.exists():
            _, parts = read_file(path.read_bytes(), path.name)
            batches.append(ingest_records(session, name, parts[None], path.name, "csv", source="synthetic"))
    return batches
