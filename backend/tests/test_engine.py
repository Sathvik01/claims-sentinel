"""Unit tests on tiny hand-built data, plus an end-to-end ground-truth check on the sample dataset."""

from datetime import date, timedelta

import pytest

from app.engine.base import Context
from app.engine.duplicates import LineIndex
from app.engine.runner import assess_claim
from app.engine.settlement import Mode, Terms, finalize, reconcile
from app.models import Claim, InvoiceLine, Member, Policy, PolicyRule, Provider, Settlement

D0 = date(2026, 10, 6)


def policy(**kw) -> Policy:
    base = dict(policy_no="P1", holder_name="A", plan="GOLD", sum_insured=500000, inception_date=date(2020, 1, 1),
                start_date=date(2026, 1, 1), end_date=date(2026, 12, 31), status="ACTIVE", deductible=0,
                copay_pct=0, room_rent_limit=5000, icu_limit=10000, initial_waiting_days=30)
    return Policy(**(base | kw))


RULES = [
    PolicyRule(rule_id="X1", plan="GOLD", clause_ref="Sec 7.3", rule_type="EXCLUSION_CODE", target_type="code",
               target="CNS-GLV", description="Gloves are non-payable"),
    PolicyRule(rule_id="F1", plan="GOLD", clause_ref="Sec 5.6", rule_type="FREQUENCY_CAP", target_type="category",
               target="physio", value=12, description="Physio 12 sessions per year"),
]
MEMBER = Member(member_id="M1", policy_no="P1", name="A", relationship="SELF", gender="F", dob=date(1960, 1, 1),
                enrolled_date=date(2020, 1, 1))
PROVIDER = Provider(provider_id="H1", name="Hosp", provider_type="HOSPITAL", city="Pune", tier=1)


def claim(cid, lines, proposed, *, incident=D0, status="PENDING", invoice=None, submitted=None, diag="K35.80",
          ctype="INPATIENT"):
    c = Claim(claim_id=cid, policy_no="P1", member_id="M1", claimant_name="A", claim_type=ctype,
              incident_date=incident, admission_date=incident, discharge_date=incident + timedelta(days=3),
              diagnosis_code=diag, provider_id="H1", invoice_no=invoice or f"INV/{cid}",
              requested_amount=sum(l.amount for l in lines), proposed_settlement=proposed,
              submitted_date=submitted or incident + timedelta(days=5), status=status)
    for i, l in enumerate(lines, 1):
        l.line_id, l.claim_id, l.provider_id = f"{cid}-L{i:02d}", cid, "H1"
    return c, lines


def line(code, cat, price, qty=1, d=D0, amount=None):
    return InvoiceLine(line_id="", claim_id="", service_date=d, service_code=code, description=code, category=cat,
                       quantity=qty, unit_price=price, amount=amount if amount is not None else price * qty)


def run(claims, pol=None, settlements=()):
    all_lines = [l for _, ls in claims for l in ls]
    ctx = Context([pol or policy()], RULES, [MEMBER], [PROVIDER], [c for c, _ in claims], all_lines, list(settlements))
    target = claims[-1][0]
    drafts, assessment = assess_claim(target, ctx, LineIndex(ctx), {})
    return {d.category: d for d in drafts}, assessment


def test_room_rent_example_from_problem_statement():
    lines = [line("ROOM", "room", 7000, qty=3), line("PRC", "procedure", 40000)]
    billed = 61000
    found, a = run([claim("C1", lines, proposed=billed)])  # handler paid as billed
    f = found["ROOM_RENT_LIMIT"]
    assert f.billed_excess == 6000  # (7,000 − 5,000) × 3 days
    assert f.leakage_amount == 6000 and f.amount_basis == "CALCULATED"
    assert a["expected_settlement"] == 55000


def test_reconciliation_identifies_skipped_copay_and_missed_item():
    terms = Terms(deductible=1000, copay_pct=20, remaining_si=500000)
    from app.engine.base import Deduction

    d_room = Deduction("r", "R04", "ROOM_RENT_LIMIT", None, "Room", 6000, "")
    d_glv = Deduction("g", "X1", "EXCLUDED_SERVICE", None, "Gloves", 480, "")
    billed = 100000
    proposed = finalize(billed - 480, terms, Mode("copay_not_applied", copay_pct=0))  # gloves deducted, room missed
    rec = reconcile(billed, [d_room, d_glv], terms, proposed)
    assert rec.explained and rec.missed_keys == {"r"}
    assert {a.title for a in rec.attributions} == {"Room", "Co-payment not applied"}
    assert round(sum(a.impact for a in rec.attributions), 2) == rec.gap


def test_correctly_deducted_items_are_not_findings():
    lines = [line("PRC", "procedure", 40000), line("CNS-GLV", "consumables", 480)]
    found, a = run([claim("C1", lines, proposed=40000)])
    assert not found
    assert next(c for c in a["checks"] if c["rule_id"] == "R03")["outcome"] == "DEDUCTED"


def test_duplicate_line_within_claim():
    lines = [line("LAB-CBC", "lab", 400), line("LAB-CBC", "lab", 400), line("PRC", "procedure", 10000)]
    found, _ = run([claim("C1", lines, proposed=10800)])
    assert found["DUPLICATE_LINE"].leakage_amount == 400


def test_exact_duplicate_of_paid_claim():
    paid = claim("C0", [line("PRC", "procedure", 30000)], 30000, status="PAID", invoice="INV-77")
    again = claim("C1", [line("PRC", "procedure", 30000)], 30000, invoice="INV-77",
                  submitted=D0 + timedelta(days=30))
    found, _ = run([paid, again], settlements=[Settlement(settlement_id="S0", claim_id="C0", approved_amount=30000,
                                                          paid_amount=30000, paid_date=D0 + timedelta(days=10))])
    assert found["DUPLICATE_CLAIM"].leakage_amount == 30000
    assert found["DUPLICATE_CLAIM"].related_claim_id == "C0"


def test_near_duplicate_flagged_for_review_not_confirmed():
    first = claim("C0", [line("PRC", "procedure", 30000), line("PHA", "pharmacy", 4000)], 34000,
                  invoice="H1/INV/2026/004512")
    shifted = [line("PRC", "procedure", 30000, d=D0 + timedelta(days=1)),
               line("PHA", "pharmacy", 4100, d=D0 + timedelta(days=1))]
    second = claim("C1", shifted, 34100, incident=D0 + timedelta(days=1), invoice="H1/INV/2026/04512",
                   submitted=D0 + timedelta(days=20))
    found, _ = run([first, second])
    f = found["NEAR_DUPLICATE"]
    assert f.finding_type == "SUSPECTED_PATTERN" and f.amount_basis == "ESTIMATED"


def test_repeated_physio_sessions_are_not_duplicates():
    a = claim("C0", [line("PHY", "physio", 600, d=D0 + timedelta(days=2 * k)) for k in range(4)], 2400,
              ctype="PHYSIO", diag="M54.5")
    b = claim("C1", [line("PHY", "physio", 600, d=D0 + timedelta(days=3 + 2 * k)) for k in range(4)], 2400,
              incident=D0 + timedelta(days=3), ctype="PHYSIO", diag="M54.5", submitted=D0 + timedelta(days=20))
    found, assessment = run([a, b])
    assert not found
    assert any(c["check"] == "D4" for c in assessment["cleared"])


def test_frequency_cap_counts_prior_sessions():
    prior = claim("C0", [line("PHY", "physio", 600, d=date(2026, 3, 1) + timedelta(days=k)) for k in range(10)],
                  6000, incident=date(2026, 3, 1), status="PAID", ctype="PHYSIO", diag="M54.5")
    now = claim("C1", [line("PHY", "physio", 600, d=D0 + timedelta(days=k)) for k in range(6)], 3600,
                ctype="PHYSIO", diag="M54.5")
    found, _ = run([prior, now])
    assert found["FREQUENCY_CAP"].billed_excess == 4 * 600


def test_missing_information_requests_clarification_without_amount():
    found, _ = run([claim("C1", [line("PRC", "procedure", 10000)], 10000, diag=None)])
    f = found["INCOMPLETE_EVIDENCE"]
    assert f.finding_type == "NEEDS_INFO" and f.leakage_amount is None
    assert "Diagnosis" in f.evidence["clarification_request"]


def test_lapsed_policy_rejects_whole_claim():
    pol = policy(status="LAPSED", end_date=D0 - timedelta(days=10))
    found, _ = run([claim("C1", [line("PRC", "procedure", 10000)], 10000)], pol=pol)
    assert found["COVERAGE"].leakage_amount == 10000


@pytest.mark.slow
def test_sample_dataset_against_ground_truth():
    from fastapi.testclient import TestClient

    from app.main import app

    from tests.conftest import auth_headers

    with TestClient(app) as c:
        assert c.post("/api/demo/load-sample", headers=auth_headers(c, "admin")).status_code == 200
        ev = c.get("/api/evaluation", headers=auth_headers(c, "admin")).json()
    assert ev["available"]
    assert ev["recall"] >= 0.95 and ev["precision"] >= 0.95
    assert ev["false_flags_on_legit"] == 0
    for s in ev["scenarios"]:
        assert s["amount_exact"] == s["amount_checked"], s["scenario"]
