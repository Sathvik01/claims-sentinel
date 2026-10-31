"""Sample PDF invoices / discharge summary for the document-extraction demo."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.synthetic.catalog import DIAGNOSES

STYLES = getSampleStyleSheet()


def _money(x: float) -> str:
    return f"Rs. {x:,.2f}"


def render_invoice(path: Path, *, provider: dict, invoice_no: str | None, invoice_date: date, patient: str,
                   member_id: str, policy_no: str, claim_type: str, admission: date | None,
                   discharge: date | None, diagnosis: str | None, lines: list[dict]) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm)
    story = [
        Paragraph(f"<b>{provider['name']}</b>", STYLES["Title"]),
        Paragraph(f"{provider['city']} &nbsp;|&nbsp; Provider ID: {provider['provider_id']}", STYLES["Normal"]),
        Spacer(1, 6 * mm),
        Paragraph("<b>FINAL BILL / TAX INVOICE</b>", STYLES["Heading2"]),
    ]
    meta = [
        ["Invoice No.", invoice_no or "", "Invoice Date", invoice_date.strftime("%d-%m-%Y")],
        ["Patient", patient, "Member ID", member_id],
        ["Policy No.", policy_no, "Claim Type", claim_type],
        ["Admission", admission.strftime("%d-%m-%Y") if admission else "-",
         "Discharge", discharge.strftime("%d-%m-%Y") if discharge else "-"],
        ["Diagnosis", f"{diagnosis} - {DIAGNOSES.get(diagnosis, '')}" if diagnosis else "-", "", ""],
    ]
    t = Table(meta, colWidths=[28 * mm, 62 * mm, 28 * mm, 62 * mm])
    t.setStyle(TableStyle([("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                           ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                           ("FONTSIZE", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    story += [t, Spacer(1, 6 * mm)]
    rows = [["#", "Code", "Description", "Date", "Qty", "Rate", "Amount"]]
    for i, l in enumerate(lines, 1):
        rows.append([i, l["service_code"], l["description"], l["service_date"].strftime("%d-%m-%Y"),
                     f"{l['quantity']:g}", _money(l["unit_price"]), _money(l["amount"])])
    total = sum(l["amount"] for l in lines)
    rows.append(["", "", "", "", "", "TOTAL", _money(total)])
    lt = Table(rows, colWidths=[8 * mm, 20 * mm, 66 * mm, 22 * mm, 10 * mm, 26 * mm, 28 * mm], repeatRows=1)
    lt.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3b57")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -2), 0.25, colors.grey),
        ("ALIGN", (4, 1), (-1, -1), "RIGHT"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    story += [lt, Spacer(1, 8 * mm),
              Paragraph(f"Amount claimed from insurer: <b>{_money(total)}</b>", STYLES["Normal"]),
              Spacer(1, 12 * mm), Paragraph("Authorised signatory — Billing Department", STYLES["Italic"])]
    doc.build(story)


def render_discharge_summary(path: Path, *, provider: dict, patient: str, member_id: str, admission: date,
                             discharge: date, diagnosis: str, text: str) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm)
    story = [Paragraph(f"<b>{provider['name']}</b>", STYLES["Title"]),
             Paragraph("<b>DISCHARGE SUMMARY</b>", STYLES["Heading2"]),
             Paragraph(f"Patient: {patient} &nbsp; Member ID: {member_id}", STYLES["Normal"]),
             Paragraph(f"Admitted: {admission:%d-%m-%Y} &nbsp; Discharged: {discharge:%d-%m-%Y}", STYLES["Normal"]),
             Paragraph(f"Final diagnosis: {diagnosis} - {DIAGNOSES.get(diagnosis, '')}", STYLES["Normal"]),
             Spacer(1, 6 * mm), Paragraph(text, STYLES["BodyText"])]
    doc.build(story)


def write_documents(gen, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    r = gen.r
    members = [m for m in gen.members if m["_policy"]["status"] == "ACTIVE"
               and (date(2026, 10, 1) - gen.cover_from(m)).days > 800]

    def new_invoice(fname: str, kind: str, m: dict, d: date, *, room_rate: float | None = None,
                    with_invoice_no: bool = True):
        prov = m["_policy"]["_home"]
        gen.r = r
        c = gen.episode(kind, m, prov, d, "PENDING")
        gen.claims.remove(c)  # document only: not part of the dataset
        if room_rate:
            for l in c.lines:
                if l["category"] == "room":
                    l.update(unit_price=room_rate, amount=room_rate * l["quantity"])
        render_invoice(out / fname, provider=prov, invoice_no=c.invoice_no if with_invoice_no else None,
                       invoice_date=c.end, patient=m["name"], member_id=m["member_id"], policy_no=m["policy_no"],
                       claim_type=c.claim_type, admission=c.admission, discharge=c.discharge,
                       diagnosis=c.diagnosis, lines=c.lines)

    m1, m2, m3 = r.sample(members, 3)
    new_invoice("invoice_new_room_over_limit.pdf", "chole", m1, date(2026, 10, 20),
                room_rate=m1["_policy"]["room_rent_limit"] + 2500)
    new_invoice("invoice_new_outpatient.pdf", "diabetes_op", m2, date(2026, 10, 24))
    new_invoice("invoice_missing_invoice_no.pdf", "urti_op", m3, date(2026, 10, 26), with_invoice_no=False)

    paid = next(c for c in gen.claims if c.status == "PAID" and not c.tags and c.claim_type == "INPATIENT")
    render_invoice(out / f"invoice_resubmitted_{paid.claim_id}.pdf", provider=paid.provider,
                   invoice_no=paid.invoice_no, invoice_date=paid.end, patient=paid.member["name"],
                   member_id=paid.member["member_id"], policy_no=paid.policy["policy_no"],
                   claim_type=paid.claim_type, admission=paid.admission, discharge=paid.discharge,
                   diagnosis=paid.diagnosis, lines=paid.lines)

    hero = next(c for c in gen.claims if c.claim_id == "CLM-DEMO-01")
    render_discharge_summary(
        out / "discharge_summary_CLM-DEMO-01.pdf", provider=hero.provider, patient=hero.member["name"],
        member_id=hero.member["member_id"], admission=hero.admission, discharge=hero.discharge,
        diagnosis=hero.diagnosis,
        text=("Patient presented with acute right iliac fossa pain. Ultrasound confirmed acute appendicitis. "
              "Laparoscopic appendectomy performed on day 2 under general anaesthesia. Post-operative course "
              "uneventful. Stayed in a private room for 3 days. Discharged in stable condition on oral "
              f"antibiotics. Follow-up after 7 days ({hero.discharge + timedelta(days=7):%d-%m-%Y})."))
