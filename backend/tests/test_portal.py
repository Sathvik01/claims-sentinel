"""Policyholder portal: scoping, filing, evidence requests, decision letter, and no internal data exposure."""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import auth_headers

INTERNAL = ("finding", "score", "integrity", "fabrication", "related", "siu", "note", "recommendation", "assigned_to",
            "confidence", "evidence")


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        assert client.post("/api/demo/load-sample", headers=auth_headers(client, "admin")).status_code == 200
        yield client


def holder_for(c, claim_id):
    policy = c.get(f"/api/investigations/{claim_id}", headers=auth_headers(c, "admin")).json()["claim"]["policy_no"]
    acct = next(a for a in c.get("/api/auth/demo-accounts").json()["accounts"] if a["policy_no"] == policy)
    return acct["username"], auth_headers(c, acct["username"])


def no_internal_keys(obj) -> bool:
    text = json.dumps(obj).lower()
    keys = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                keys.add(k.lower())
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return not any(any(word in k for word in INTERNAL) for k in keys) and "siu" not in text


def test_scoping_and_staff_endpoints_refused(c):
    _, ph = holder_for(c, "CLM-DEMO-01")
    home = c.get("/api/portal/me", headers=ph).json()
    assert any(x["claim_id"] == "CLM-DEMO-01" for x in home["claims"]) and home["policy"]["remaining"] > 0
    assert no_internal_keys(home)
    assert c.get("/api/portal/claims/CLM-000048", headers=ph).status_code == 404  # another policy
    assert c.get("/api/investigations/CLM-DEMO-01", headers=ph).status_code == 403
    assert c.get("/api/findings", headers=ph).status_code == 403


def test_file_claim_request_respond_decide_and_letter(c):
    username, ph = holder_for(c, "CLM-DEMO-01")
    home = c.get("/api/portal/me", headers=ph).json()
    member = home["members"][0]["member_id"]
    hospital = c.get("/api/portal/providers", headers=ph).json()[0]["provider_id"]
    draft = c.post("/api/portal/claims", headers=ph, json={
        "member_id": member, "claim_type": "INPATIENT", "provider_id": hospital,
        "admission_date": "2026-10-20", "discharge_date": "2026-10-23", "payout_account": "HDFC0123456 XXXXXX4321"}).json()
    cid = draft["claim_id"]
    assert draft["status"] == "Draft"
    room_limit = home["policy"]["room_rent_limit"]
    lines = [{"description": "Room rent", "service_code": "ROOM-WRD", "category": "room", "date": "2026-10-20",
              "quantity": 3, "unit_price": room_limit + 2000, "amount": 3 * (room_limit + 2000)},
             {"description": "Appendectomy", "service_code": "PRC-APP", "category": "procedure", "date": "2026-10-21",
              "quantity": 1, "unit_price": 45000, "amount": 45000}]
    sub = c.post(f"/api/portal/claims/{cid}/submit", headers=ph,
                 json={"lines": lines, "invoice_no": "TEST/INV/77", "diagnosis_code": "K35.80", "declaration": True}).json()
    assert sub["status"] == "Under review" and no_internal_keys(sub)

    admin = auth_headers(c, "admin")
    ws = c.get(f"/api/investigations/{cid}", headers=admin).json()
    assert ws["state"] == "INVESTIGATION" and any(f["category"] == "ROOM_RENT_LIMIT" for f in ws["findings"])
    analyst = auth_headers(c, ws["assigned_to"])
    c.post(f"/api/claims/{cid}/evidence-requests", headers=analyst,
           json={"message": "Please upload the discharge summary", "doc_types": ["DISCHARGE_SUMMARY"]})
    view = c.get(f"/api/portal/claims/{cid}", headers=ph).json()
    assert view["status"] == "Action needed" and view["requests"][0]["status"] == "OPEN"
    assert any("Action needed" in n["message"] for n in c.get("/api/notifications", headers=ph).json()["items"])

    r = c.post(f"/api/portal/requests/{view['requests'][0]['id']}/respond", headers=ph,
               data={"message": "Discharge summary will follow from the hospital; stay was 3 nights."}).json()
    assert r["status"] == "Under review" and r["requests"][0]["status"] == "RESPONDED"

    ws = c.get(f"/api/investigations/{cid}", headers=analyst).json()
    for f in ws["findings"]:
        decision = "ACCEPT_CORRECTION" if f["recommendations"] else "CONFIRM_DISCREPANCY"
        if f["finding_type"] != "CONFIRMED_VIOLATION":
            decision = "FALSE_POSITIVE"
        c.post(f"/api/findings/{f['id']}/decision", headers=analyst, json={"decision": decision, "reason": "reviewed"})
    out = c.post(f"/api/investigations/{cid}/decision", headers=analyst,
                 json={"decision": "PARTIALLY_APPROVED", "reason": "room capped at policy limit"}).json()
    assert out["state"] == "PARTIALLY_APPROVED"

    view = c.get(f"/api/portal/claims/{cid}", headers=ph).json()
    letter = view["letter"]
    assert letter["decision"] == "Approved with adjustments" and letter["approved_amount"] < view["amount_claimed"]
    assert any("daily limit" in a["explanation"] for a in letter["adjustments"])
    assert no_internal_keys(view)
    assert [t["status"] for t in view["timeline"]][:2] == ["Draft", "Submitted"]
    pdf = c.get(f"/api/portal/claims/{cid}/letter.pdf", headers=ph)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    again = c.post(f"/api/portal/claims/{cid}/reconsideration", headers=ph, json={"reason": "room was the only one free"}).json()
    assert again["status"] == "Under review"
