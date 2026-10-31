"""Level 2 investigation workflow through the HTTP API, role by role."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.main import app
from app.models import AuditEvent
from tests.conftest import auth_headers


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        assert client.post("/api/demo/load-sample", headers=auth_headers(client, "admin")).status_code == 200
        yield client


def ws(c, claim, who):
    return c.get(f"/api/investigations/{claim}", headers=auth_headers(c, who)).json()


def finding(w, category):
    return next(f for f in w["findings"] if f["category"] == category)


def test_hero_claim_investigation_end_to_end(c):
    admin = auth_headers(c, "admin")
    w = ws(c, "CLM-DEMO-01", "admin")
    assert w["state"] == "INVESTIGATION" and w["assigned_to"] in ("analyst", "analyst2")
    me, other = w["assigned_to"], ({"analyst", "analyst2"} - {w["assigned_to"]}).pop()
    h, h_other, senior = auth_headers(c, me), auth_headers(c, other), auth_headers(c, "senior")
    room = finding(w, "ROOM_RENT_LIMIT")
    rec = room["recommendations"][0]
    assert (rec["original_amount"], rec["corrected_amount"], rec["settlement_impact"]) == (21000, 15000, 4800)
    assert rec["clause_text"] and rec["calculation"]

    url = lambda f: f"/api/findings/{f['id']}/decision"  # noqa: E731
    assert c.post(url(room), json={"decision": "CONFIRM_DISCREPANCY"}, headers=h_other).status_code == 403  # not assignee
    assert c.post(url(room), json={"decision": "CONFIRM_DISCREPANCY"}, headers=auth_headers(c, "handler")).status_code == 403
    assert c.post(url(room), json={"decision": "ACCEPT_CORRECTION"}, headers=h).status_code == 200
    gloves, dup = finding(w, "EXCLUDED_SERVICE"), finding(w, "DUPLICATE_LINE")
    c.post(url(gloves), json={"decision": "CONFIRM_DISCREPANCY"}, headers=h)
    assert c.post(url(dup), json={"decision": "FALSE_POSITIVE"}, headers=h).status_code == 422  # reason required
    c.post(url(dup), json={"decision": "FALSE_POSITIVE", "reason": "second CBC ordered after transfusion"}, headers=h)

    copay = finding(w, "SETTLEMENT_MISCALC")
    r = c.post(url(copay), json={"decision": "ACCEPT_CORRECTION", "override_impact": 5000,
                                 "reason": "co-pay waived under senior-citizen endorsement"}, headers=h).json()
    pending = finding(r, "SETTLEMENT_MISCALC")
    assert pending["status"] == "PENDING_APPROVAL"  # override beyond threshold → maker-checker
    assert pending["recommendations"][0]["settlement_impact"] == 20378  # original recommendation kept
    dec_id = pending["decisions"][-1]["id"]

    body = {"decision": "PARTIALLY_APPROVED", "reason": "pay per policy"}
    assert c.post("/api/investigations/CLM-DEMO-01/decision", json=body, headers=h).status_code == 409  # open item
    assert c.post(f"/api/finding-decisions/{dec_id}/approval", json={"approve": True, "reason": "ok"},
                  headers=h).status_code == 403  # analyst lacks approval rights
    r = c.post(f"/api/finding-decisions/{dec_id}/approval",
               json={"approve": False, "reason": "endorsement not on file"}, headers=senior).json()
    assert finding(r, "SETTLEMENT_MISCALC")["status"] == "OPEN"
    c.post(url(copay), json={"decision": "ACCEPT_CORRECTION"}, headers=h)

    r = c.post("/api/investigations/CLM-DEMO-01/decision", json=body, headers=h).json()
    assert r["state"] == "PARTIALLY_APPROVED"
    # room 4,800 + gloves 384 + copay 20,378 deducted; the dismissed duplicate (320) is paid
    assert r["decisions"][-1]["approved_amount"] == round(101890 - 4800 - 384 - 20378, 2)
    reg = c.get("/api/claims", params={"ids": "CLM-DEMO-01"}, headers=admin).json()["items"][0]
    assert reg["review_status"] == "PARTIALLY_APPROVED"

    rep = c.get("/api/investigations/CLM-DEMO-01/report.json", headers=senior).json()
    assert rep["audit_chain"]["valid"]
    actions = [e["action"] for e in rep["timeline"]]
    for a in ("FINDING_ACCEPT_CORRECTION", "OVERRIDE_DECLINED", "FINDING_FALSE_POSITIVE", "CLAIM_DECIDED"):
        assert a in actions
    pdf = c.get("/api/investigations/CLM-DEMO-01/report.pdf", headers=senior)
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_escalation_to_siu_and_evidence_request(c):
    reg = c.get("/api/claims", params={"state": "INVESTIGATION", "limit": 50}, headers=auth_headers(c, "admin")).json()
    target = next(r for r in reg["items"] if r["claim_id"] != "CLM-DEMO-01" and r["findings_open"] >= 2)
    cid, me = target["claim_id"], target["assigned_to"]
    h = auth_headers(c, me)
    w = ws(c, cid, me)
    f1, f2 = w["findings"][0], w["findings"][1]
    r = c.post(f"/api/findings/{f1['id']}/decision", headers=h,
               json={"decision": "ESCALATE", "target": "SIU", "reason": "pattern across policies"}).json()
    assert r["state"] == "ESCALATED_SIU"
    esc = r["escalations"][0]
    assert c.post(f"/api/escalations/{esc['id']}/resolve", headers=auth_headers(c, "senior"),
                  json={"resolution": "x"}).status_code == 403  # SIU referrals resolved by SIU
    r = c.post(f"/api/escalations/{esc['id']}/resolve", headers=auth_headers(c, "siu"),
               json={"resolution": "provider confirmed separate visits", "outcome": "NOT_SUBSTANTIATED"}).json()
    assert r["state"] == "INVESTIGATION"
    r = c.post(f"/api/findings/{f2['id']}/decision", headers=h,
               json={"decision": "REQUEST_EVIDENCE", "message": "Upload the itemised pharmacy bill", "doc_types": ["INVOICE"]}).json()
    assert r["state"] == "INFO_REQUESTED" and r["evidence_requests"][0]["status"] == "OPEN"
    siu_note = c.post(f"/api/claims/{cid}/notes", headers=auth_headers(c, "siu"), json={"text": "watchlist", "visibility": "SIU"})
    assert siu_note.status_code == 200
    assert all(n["visibility"] != "SIU" for n in ws(c, cid, me)["notes"])  # analysts don't see SIU notes


def test_authority_limit_routes_to_senior(c):
    reg = c.get("/api/claims", params={"state": "INVESTIGATION", "limit": 5000}, headers=auth_headers(c, "admin")).json()
    big = next(r for r in reg["items"] if (r["proposed_settlement"] or 0) > 200000 and r["findings_open"] >= 1
               and r["claim_id"] != "CLM-DEMO-01")
    me = auth_headers(c, big["assigned_to"])
    w = ws(c, big["claim_id"], big["assigned_to"])
    for f in w["findings"]:
        c.post(f"/api/findings/{f['id']}/decision", headers=me, json={"decision": "FALSE_POSITIVE", "reason": "reviewed"})
    r = c.post(f"/api/investigations/{big['claim_id']}/decision", headers=me,
               json={"decision": "APPROVED", "reason": "all clear"}).json()
    assert r["state"] == "PENDING_APPROVAL"
    r = c.post(f"/api/investigations/{big['claim_id']}/approval", headers=auth_headers(c, "senior"),
               json={"approve": True, "reason": "verified"}).json()
    assert r["state"] == "APPROVED"


def test_audit_chain_detects_tampering(c):
    admin = auth_headers(c, "admin")
    assert c.get("/api/audit/verify", headers=admin).json()["valid"]
    with Session(engine) as s:
        e = s.exec(select(AuditEvent).where(AuditEvent.action == "CLAIM_DECIDED")).first()
        e.reason = "edited later"
        s.add(e)
        s.commit()
    v = c.get("/api/audit/verify", headers=admin).json()
    assert not v["valid"] and v["reason"] == "content changed after recording"
