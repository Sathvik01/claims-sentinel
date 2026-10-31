"""Evidence with provenance: every value extracted from a document keeps its page and the exact source text.
A value is `verified` only when that text is found on the page; unverified values never drive amounts.

Two extraction paths share one field store:
- template parsing of known hospital bill / discharge-summary layouts (deterministic, from the PDF text layer)
- LLM extraction (ChatGroq) for anything else, verified afterwards by locating each value in the page text."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader
from sqlmodel import Session, delete, select

from app.models import Document, ExtractedField

HEADER_LABELS = {"Invoice No.": "invoice_no", "Invoice Date": "invoice_date", "Patient": "patient_name",
                 "Member ID": "member_id", "Policy No.": "policy_no", "Claim Type": "claim_type",
                 "Admission": "admission_date", "Discharge": "discharge_date", "Diagnosis": "diagnosis"}
DATE = re.compile(r"^\d{2}-\d{2}-\d{4}$")


def page_texts(path: str | Path) -> list[str]:
    return [p.extract_text() or "" for p in PdfReader(str(path)).pages]


def _money(s: str) -> float:
    return float(s.replace("Rs.", "").replace(",", "").strip())


def _iso(d: str) -> str:
    return datetime.strptime(d, "%d-%m-%Y").date().isoformat()


def parse_invoice(texts: list[str]) -> list[dict]:
    """Fields from the hospital bill layout: header label/value pairs and the itemised table."""
    out: list[dict] = []
    for page, text in enumerate(texts):
        toks = [t.strip() for t in text.splitlines()]
        m = re.search(r"Provider ID:\s*(\S+)", text)
        if m:
            out.append(dict(path="provider_id", value=m.group(1), page=page, source_text=m.group(0)))
        for i, t in enumerate(toks[:-1]):
            if t in HEADER_LABELS and toks[i + 1] not in HEADER_LABELS and toks[i + 1] != "#":
                v = toks[i + 1]
                key = HEADER_LABELS[t]
                value = _iso(v) if DATE.match(v) else (v.split(" - ")[0] if key == "diagnosis" else v)
                out.append(dict(path=key, value=value, page=page, source_text=f"{t} {v}"))
        if "Amount" in toks and "TOTAL" in toks:
            i, end = toks.index("Amount") + 1, toks.index("TOTAL")
            n = 0
            while i + 6 < end + 1 and toks[i].isdigit():
                idx, code, desc, d, qty, rate, amt = toks[i:i + 7]
                row = " | ".join((code, desc, d, qty, rate, amt))
                for k, v in (("code", code), ("description", desc), ("date", _iso(d)), ("quantity", qty),
                             ("unit_price", _money(rate)), ("amount", _money(amt))):
                    out.append(dict(path=f"lines[{n}].{k}", value=str(v), page=page, source_text=row))
                n, i = n + 1, i + 7
            if end + 1 < len(toks):
                out.append(dict(path="total", value=str(_money(toks[end + 1])), page=page,
                                source_text=f"TOTAL {toks[end + 1]}"))
    return out


def parse_discharge(texts: list[str]) -> list[dict]:
    out: list[dict] = []
    patterns = [
        ("admission_date", r"Admitted:\s*(\d{2}-\d{2}-\d{4})", _iso),
        ("discharge_date", r"Discharged:\s*(\d{2}-\d{2}-\d{4})", _iso),
        ("length_of_stay", r"Length of stay:\s*(\d+) day", str),
        ("room", r"Room:\s*([^\n]+)", str),
        ("diagnosis", r"Final diagnosis:\s*(\S+)", str),
        ("member_id", r"Member ID:\s*(\S+)", str),
    ]
    for page, text in enumerate(texts):
        for key, pat, conv in patterns:
            m = re.search(pat, text)
            if m:
                out.append(dict(path=key, value=conv(m.group(1).strip()), page=page, source_text=m.group(0).strip()))
    return out


def _variants(value: str) -> list[str]:
    v = str(value)
    out = [v]
    try:  # dates appear as dd-mm-yyyy on the documents
        out.append(datetime.fromisoformat(v).strftime("%d-%m-%Y"))
    except ValueError:
        pass
    try:  # amounts appear as 12,345.00 (Indian or western grouping)
        f = float(v)
        out += [f"{f:,.2f}", f"{f:.2f}", f"{f:g}"]
    except ValueError:
        pass
    return out


def locate(value, texts: list[str]) -> tuple[int | None, str | None]:
    for page, text in enumerate(texts):
        flat = text.replace(",", "")
        for v in _variants(value):
            if v and (v in text or v.replace(",", "") in flat):
                i = text.find(v) if v in text else -1
                snippet = text[max(0, i - 30): i + len(v) + 30].replace("\n", " ") if i >= 0 else v
                return page, snippet.strip()
    return None, None


def fields_from_llm(extraction: dict, texts: list[str]) -> list[dict]:
    """Flatten an LLM extraction and verify each value against the PDF text (images: unverifiable)."""
    flat = {k: extraction.get(k) for k in ("invoice_no", "invoice_date", "patient_name", "member_id", "policy_no",
                                         "claim_type", "admission_date", "discharge_date", "diagnosis_code",
                                         "provider_id", "total_amount")}
    out = []
    for k, v in flat.items():
        if v not in (None, ""):
            out.append(dict(path={"diagnosis_code": "diagnosis", "total_amount": "total"}.get(k, k), value=str(v)))
    for i, ln in enumerate(extraction.get("lines") or []):
        for k in ("service_code", "description", "service_date", "quantity", "unit_price", "amount"):
            if ln.get(k) not in (None, ""):
                out.append(dict(path=f"lines[{i}].{ {'service_code': 'code', 'service_date': 'date'}.get(k, k)}",
                                value=str(ln[k])))
    for f in out:
        page, snippet = locate(f["value"], texts) if texts else (None, None)
        f.update(page=page, source_text=snippet)
    return out


def store(session: Session, doc: Document, fields: list[dict], method: str, model: str | None = None) -> int:
    session.exec(delete(ExtractedField).where(ExtractedField.doc_id == doc.id))
    for f in fields:
        session.add(ExtractedField(doc_id=doc.id, claim_id=doc.claim_id, doc_type=doc.doc_type, path=f["path"],
                                   value=f["value"], page=f.get("page"), source_text=f.get("source_text"),
                                   verified=f.get("source_text") is not None and f.get("page") is not None,
                                   method=method, model=model))
    return len(fields)


def evidence_by_claim(session: Session) -> dict[str, dict]:
    """{claim_id: {doc_type: {"doc_id", "header": {path: field}, "lines": [{key: field}]}}} for the engine."""
    out: dict[str, dict] = {}
    for f in session.exec(select(ExtractedField).where(ExtractedField.claim_id.is_not(None))):
        d = out.setdefault(f.claim_id, {}).setdefault(f.doc_type, {"doc_id": f.doc_id, "header": {}, "lines": {}})
        m = re.match(r"lines\[(\d+)\]\.(\w+)", f.path)
        if m:
            d["lines"].setdefault(int(m.group(1)), {})[m.group(2)] = f
        else:
            d["header"][f.path] = f
    for docs in out.values():
        for d in docs.values():
            d["lines"] = [d["lines"][i] for i in sorted(d["lines"])]
    return out


def load_claim_evidence(session: Session, directory: Path) -> int:
    """Register sample_data/documents/claims/{claim_id}_{invoice|discharge}.pdf and parse them."""
    n = 0
    for pdf in sorted(directory.glob("*.pdf")):
        claim_id, kind = pdf.stem.rsplit("_", 1)
        doc_type = "INVOICE" if kind == "invoice" else "DISCHARGE_SUMMARY"
        doc = Document(filename=pdf.name, content_type="application/pdf", path=str(pdf), status="CONFIRMED",
                       claim_id=claim_id, doc_type=doc_type, source="synthetic")
        session.add(doc)
        session.flush()
        texts = page_texts(pdf)
        store(session, doc, parse_invoice(texts) if doc_type == "INVOICE" else parse_discharge(texts), "template")
        n += 1
    session.commit()
    return n
