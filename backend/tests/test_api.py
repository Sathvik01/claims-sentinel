"""Ingestion validation, review workflow and the document → claim path through the HTTP API."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        assert c.post("/api/demo/load-sample").status_code == 200
        yield c


def test_dirty_rows_are_reported_not_dropped(client):
    path = settings.sample_data_dir / "claims_dirty.csv"
    with path.open("rb") as fh:
        b = client.post("/api/ingest/claims", files={"file": ("claims_dirty.csv", fh, "text/csv")}).json()[0]
    errs = client.get(f"/api/ingest/batches/{b['id']}/errors").json()
    rejected_rows = {e["row_number"] for e in errs if e["severity"] == "ERROR"}
    assert b["accepted"] + b["rejected"] == b["received"] == 25
    assert len(rejected_rows) == b["rejected"]  # every rejected row has at least one reason


def test_review_actions_and_roles(client):
    fid = client.get("/api/findings", params={"category": "ROOM_RENT_LIMIT", "limit": 1}).json()["items"][0]["id"]
    assert client.post(f"/api/findings/{fid}/action", json={"action": "CONFIRM"},
                       headers={"X-Role": "viewer"}).status_code == 403
    assert client.post(f"/api/findings/{fid}/action", json={"action": "DISMISS"}).status_code == 422
    r = client.post(f"/api/findings/{fid}/action", json={"action": "CORRECT", "amount": 123, "reason": "ok"}).json()
    assert r["finding"]["status"] == "CORRECTED" and r["finding"]["effective_amount"] == 123
    client.post("/api/analysis/run")
    again = client.get(f"/api/findings/{fid}").json()
    assert again["finding"]["status"] == "CORRECTED" and len(again["actions"]) == 1


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
