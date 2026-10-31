"""Ingestion validation, review workflow and the document → claim path through the HTTP API."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from tests.conftest import auth_headers


@pytest.fixture(scope="module")
def client():
    """Signed in as the ops admin (ingestion, analysis); other roles pass their own headers."""
    with TestClient(app) as c:
        c.headers.update(auth_headers(c, "admin"))
        assert c.post("/api/demo/load-sample").status_code == 200
        c.headers.update(auth_headers(c, "admin"))  # users were re-seeded by the reset
        yield c


@pytest.fixture(scope="module")
def analyst(client):
    return auth_headers(client, "analyst")


def test_unauthenticated_and_wrong_role_are_refused(client):
    assert client.get("/api/claims", headers={"Authorization": ""}).status_code == 401
    handler = auth_headers(client, "handler")
    assert client.post("/api/analysis/run", headers=handler).status_code == 403
    assert client.get("/api/claims", headers=handler).status_code == 200


def test_dirty_rows_are_reported_not_dropped(client):
    path = settings.sample_data_dir / "claims_dirty.csv"
    with path.open("rb") as fh:
        b = client.post("/api/ingest/claims", files={"file": ("claims_dirty.csv", fh, "text/csv")}).json()[0]
    errs = client.get(f"/api/ingest/batches/{b['id']}/errors").json()
    rejected_rows = {e["row_number"] for e in errs if e["severity"] == "ERROR"}
    assert b["accepted"] + b["rejected"] == b["received"] == 25
    assert len(rejected_rows) == b["rejected"]  # every rejected row has at least one reason


def test_confirmed_document_becomes_claim_and_is_analysed(client):
    pdf = settings.sample_data_dir / "documents" / "invoice_new_room_over_limit.pdf"
    with pdf.open("rb") as fh:
        doc = client.post("/api/documents", files={"file": (pdf.name, fh, "application/pdf")}).json()
    member = client.get("/api/claims/CLM-DEMO-01").json()["claim"]
    extraction = {
        "document_type": "invoice", "provider_id": member["provider_id"], "invoice_no": "TEST/INV/1",
        "invoice_date": "2026-10-20", "patient_name": member["claimant_name"], "member_id": member["member_id"],
        "policy_no": member["policy_no"], "claim_type": "INPATIENT", "admission_date": "2026-10-18",
        "discharge_date": "2026-10-20", "diagnosis_code": "K80.20",
        "lines": [{"service_code": "ROOM-WRD", "description": "Room", "service_date": "2026-10-18", "quantity": 2,
                   "unit_price": 7500, "amount": 15000, "category": "room"},
                  {"service_code": "PRC-LCH", "description": "Lap chole", "service_date": "2026-10-19",
                   "quantity": 1, "unit_price": 60000, "amount": 60000, "category": "procedure"}],
    }
    r = client.post(f"/api/documents/{doc['id']}/confirm", json={"extraction": extraction,
                                                                 "proposed_settlement": 60000}).json()
    assert r["ok"], r
    cats = {f["category"] for f in r["findings"]}
    assert "ROOM_RENT_LIMIT" in cats


def test_claims_register_counts(client):
    reg = client.get("/api/claims", params={"limit": 5000}).json()
    pending = client.get("/api/dashboard/summary").json()["claims_processed"]
    assert sum(reg["counts"].values()) == reg["total"] == pending
    hero = next(r for r in reg["items"] if r["claim_id"] == "CLM-DEMO-01")
    assert hero["review_status"] == "OPEN" and hero["findings_confirmed"] == 4 and hero["state"] == "INVESTIGATION"
