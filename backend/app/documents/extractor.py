"""Invoice / discharge-summary extraction with an LLM (ChatGroq) structured output, and conversion of a
reviewed extraction into claim + invoice-line rows that go through normal ingestion validation."""

from __future__ import annotations

import io
from typing import Literal

from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.ai import llm
from app.synthetic.catalog import SERVICES, category_of


class ExtractedLine(BaseModel):
    service_code: str | None = Field(None, description="Billing code exactly as printed, e.g. LAB-CBC")
    description: str
    service_date: str | None = Field(None, description="YYYY-MM-DD")
    quantity: float = 1
    unit_price: float
    amount: float
    category: Literal["room", "icu", "consultation", "lab", "radiology", "procedure", "pharmacy", "consumables",
                      "physio", "dialysis", "chemo", "ambulance", "other"]


class InvoiceExtraction(BaseModel):
    document_type: Literal["invoice", "discharge_summary", "other"]
    provider_id: str | None = None
    provider_name: str | None = None
    invoice_no: str | None = Field(None, description="null if the document shows no invoice number")
    invoice_date: str | None = Field(None, description="YYYY-MM-DD")
    patient_name: str | None = None
    member_id: str | None = None
    policy_no: str | None = None
    claim_type: Literal["INPATIENT", "DAYCARE", "OUTPATIENT", "PHYSIO", "DIALYSIS"] | None = None
    admission_date: str | None = Field(None, description="YYYY-MM-DD")
    discharge_date: str | None = Field(None, description="YYYY-MM-DD")
    diagnosis_code: str | None = Field(None, description="ICD-10 code as printed")
    diagnosis_text: str | None = None
    total_amount: float | None = None
    lines: list[ExtractedLine] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list,
                                      description="Fields a claims assessor would need that are absent or illegible")
    notes: str | None = None


SYSTEM = (
    "You extract data from Indian health-insurance claim documents (hospital bills, discharge summaries). "
    "Copy values exactly as printed. Convert dates to YYYY-MM-DD. Amounts are plain numbers in INR. "
    "Never invent a value: if a field is not visible, return null and list it in missing_fields. "
    "Return every invoice line; do not merge or drop lines, even if two lines look identical."
)


MIN_TEXT_CHARS = 40  # below this a PDF is treated as scanned


def extract(data: bytes, mime_type: str) -> InvoiceExtraction:
    """Text PDFs → text model; images and scanned PDFs (embedded page images) → vision model."""
    if mime_type == "application/pdf":
        reader = PdfReader(io.BytesIO(data))
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        if len(text.strip()) >= MIN_TEXT_CHARS:
            out = llm.generate(InvoiceExtraction, f"Extract this claim document. Document text:\n\n{text}", SYSTEM)
        else:
            images = [img for page in reader.pages[:3] for img in page.images][:3]
            if not images:
                raise ValueError("PDF has no extractable text or page images")
            out = llm.generate(InvoiceExtraction,
                               [{"type": "text", "text": "Extract this claim document."},
                                *(llm.image_part(img.data, "image/png" if img.name.lower().endswith(".png")
                                                 else "image/jpeg") for img in images)],
                               SYSTEM, vision=True)
    else:
        out = llm.generate(InvoiceExtraction, [{"type": "text", "text": "Extract this claim document."},
                                               llm.image_part(data, mime_type)], SYSTEM, vision=True)
    for ln in out.lines:  # known codes take the catalogue category
        cat = category_of(ln.service_code or "")
        if cat:
            ln.category = cat
            ln.description = ln.description or SERVICES[ln.service_code][0]
    return out


def to_rows(ext: InvoiceExtraction, claim_id: str, proposed: float | None, submitted: str) -> tuple[dict, list[dict]]:
    total = round(sum(l.amount for l in ext.lines), 2) if ext.lines else (ext.total_amount or 0)
    claim = dict(
        claim_id=claim_id, policy_no=ext.policy_no, member_id=ext.member_id, claimant_name=ext.patient_name,
        claim_type=ext.claim_type, incident_date=ext.admission_date or ext.invoice_date,
        admission_date=ext.admission_date, discharge_date=ext.discharge_date, diagnosis_code=ext.diagnosis_code,
        provider_id=ext.provider_id, invoice_no=ext.invoice_no, requested_amount=total,
        proposed_settlement=proposed, submitted_date=submitted, status="PENDING",
    )
    lines = [dict(line_id=f"{claim_id}-L{i:02d}", claim_id=claim_id, invoice_no=ext.invoice_no,
                  provider_id=ext.provider_id, service_date=l.service_date or ext.admission_date or ext.invoice_date,
                  service_code=l.service_code or f"UNK-{i:02d}", description=l.description, category=l.category,
                  quantity=l.quantity, unit_price=l.unit_price, amount=l.amount)
             for i, l in enumerate(ext.lines, 1)]
    return claim, lines
