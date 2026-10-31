"""Runs every detector over the pending claims and persists findings + per-claim assessments."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session, delete, select

from app.config import settings
from app.engine import base as B
from app.engine.base import Check, Context, Deduction, FindingDraft, inr
from app.engine.doc_compare import compare as compare_documents
from app.engine.correlation import build_links, clusters, related_findings
from app.engine.duplicates import LineIndex, duplicate_lines, exact_duplicate_claim, near_duplicates
from app.engine.provider_outliers import build_outliers, provider_outlier_findings
from app.engine.rules import RULE_FAMILIES, admissibility, completeness, line_deductions
from app.engine.settlement import Terms, finalize, reconcile
from app import audit
from app.engine.recommendations import recommend
from app.models import AnalysisRun, Claim, ClaimAssessment, ClaimLink, Document, DocumentScan, Finding, Recommendation

FAMILY_BY_CATEGORY = {
    B.COVERAGE: "R01", B.WAITING_PERIOD: "R02", B.EXCLUDED_DIAGNOSIS: "R03", B.EXCLUDED_SERVICE: "R03",
    B.ROOM_RENT_LIMIT: "R04", B.ICU_LIMIT: "R05", B.SUB_LIMIT: "R06", B.FREQUENCY_CAP: "R06",
    B.ARITHMETIC_ERROR: "R10", B.DUPLICATE_CLAIM: "D1", B.DOCUMENT_MISMATCH: "E1",
}
WHOLE_CLAIM_CATEGORIES = {B.DUPLICATE_CLAIM, B.COVERAGE, B.WAITING_PERIOD, B.EXCLUDED_DIAGNOSIS}


def _family(d: Deduction) -> str:
    return d.rule_id if d.rule_id in ("D2", "D3") else FAMILY_BY_CATEGORY.get(d.category, d.rule_id)


def severity(f: FindingDraft) -> str:
    amt = f.leakage_amount or 0
    if f.finding_type == B.NEEDS_INFO:
        return "LOW"
    if f.finding_type == B.CONFIRMED and (f.category in WHOLE_CLAIM_CATEGORIES or amt >= settings.severity_high):
        return "HIGH"
    if amt >= settings.severity_high and f.confidence >= 0.8:
        return "HIGH"
    if amt >= settings.severity_medium or (f.finding_type == B.SUSPECTED and f.confidence >= 0.85):
        return "MEDIUM"
    return "LOW"


def _deduction_finding(d: Deduction, impact: float | None, note: str) -> FindingDraft:
    calc = d.calculation
    if impact is not None:
        calc += f" {note} {inr(impact)}."
    return FindingDraft(
        category=d.category, rule_id=d.rule_id, title=d.title, finding_type=B.CONFIRMED,
        amount_basis=B.CALCULATED, calculation=calc, clause_ref=d.clause_ref, billed_excess=d.amount,
        leakage_amount=impact, line_ids=d.line_ids, related_claim_id=d.related_claim_id, evidence=d.evidence,
    )


def assess_claim(claim: Claim, ctx: Context, index: LineIndex, outliers: dict) -> tuple[list[FindingDraft], dict]:
    lines = sorted(ctx.lines_by_claim.get(claim.claim_id, []), key=lambda l: l.line_id)
    drafts, flags = completeness(claim, lines)
    notes: dict[str, str] = {}
    cleared: list[dict] = []
    billed = round(sum(l.amount for l in lines), 2) if lines else claim.requested_amount
    policy = ctx.policies.get(claim.policy_no)
    if policy is None:
        drafts.append(FindingDraft(B.INCOMPLETE_EVIDENCE, "C02", f"Policy {claim.policy_no} not on file",
                                   B.NEEDS_INFO, B.NOT_DETERMINABLE,
                                   "Policy terms are required to validate the claim; no amount assumed.",
                                   evidence={"clarification_request": f"Provide policy schedule for {claim.policy_no}."}))
        return drafts, {"billed_amount": billed, "checks": [], "cleared": [], "waterfall": []}

    remaining = {l.line_id: l.amount for l in lines}
    whole = admissibility(claim, ctx, policy, flags, billed, notes)
    dup_claim = None
    if whole is None and lines:
        dup_claim = exact_duplicate_claim(claim, ctx, billed)
        whole = dup_claim
    doc_rows: list[dict] = []
    if whole:
        # Line rules first, so the whole-claim deduction is exactly what would otherwise be paid.
        deductions = line_deductions(claim, ctx, policy, lines, remaining, notes) if lines else []
        whole.amount = round(sum(remaining.values()), 2) if lines else billed
        whole.calculation += f" Remaining admissible amount {inr(whole.amount)} is not payable."
        for k in remaining:
            remaining[k] = 0.0
        deductions.append(whole)
        for fam in RULE_FAMILIES:
            notes.setdefault(fam, "NA: claim inadmissible as a whole")
    else:
        notes["D1"] = "No matching invoice or incident on file"
        deductions = duplicate_lines(claim, ctx, lines, remaining, index, notes, cleared)
        ev = getattr(ctx, "evidence", {}).get(claim.claim_id)
        if ev:  # align the claim with its documents before applying policy limits
            doc_rows, doc_deds, doc_drafts = compare_documents(claim, lines, ev, remaining)
            deductions += doc_deds
            drafts.extend(doc_drafts)
            notes["E1"] = f"{sum(r['status'] == 'MATCH' for r in doc_rows)} of {len(doc_rows)} compared values match"
        else:
            doc_rows = []
            notes["E1"] = "NA: no supporting documents on file"
        deductions += line_deductions(claim, ctx, policy, lines, remaining, notes)

    terms = Terms(policy.deductible, policy.copay_pct,
                  round(policy.sum_insured - ctx.policy_year_paid(policy), 2))
    rec = reconcile(billed, deductions, terms, claim.proposed_settlement, settings.amount_tolerance)

    mode_errors: list[str] = []
    if claim.proposed_settlement is None:
        # Pre-payment view: what each deduction is worth if the claim were paid as billed.
        running_adm = billed - sum(d.amount for d in deductions)
        running = finalize(running_adm, terms)
        for d in deductions:
            running_adm += d.amount
            pay = finalize(running_adm, terms)
            drafts.append(_deduction_finding(d, round(pay - running, 2), "Payable impact if paid as billed:"))
            running = pay
    else:
        for a in rec.attributions:
            if a.deduction is not None:
                f = _deduction_finding(a.deduction, a.impact, "Overpayment in proposed settlement:")
                if not rec.explained:
                    f.evidence = f.evidence | {"allocation": "proportional (gap not reproducible by a single error)"}
            else:
                mode_errors.append(a.rule_id)
                f = FindingDraft(B.SETTLEMENT_MISCALC, a.rule_id, a.title, B.CONFIRMED, B.CALCULATED,
                                 a.calculation, clause_ref="Sec 8 (Settlement computation)",
                                 billed_excess=a.impact, leakage_amount=a.impact,
                                 evidence={"terms": terms.__dict__, "expected": rec.expected,
                                           "proposed": rec.proposed})
            f.evidence = f.evidence | {"reconciliation": rec.summary}
            drafts.append(f)

    if not dup_claim and not whole:
        drafts.extend(near_duplicates(claim, ctx, None, cleared))
    drafts.extend(provider_outlier_findings(claim, ctx, outliers))

    # rules-evaluated checklist
    checks: list[Check] = []
    for fam, name in RULE_FAMILIES.items():
        fam_deds = [d for d in deductions if _family(d) == fam]
        missed = [d for d in fam_deds if d.key in rec.missed_keys or claim.proposed_settlement is None]
        if fam in ("R07", "R08", "R09"):
            bad = any(fam in m for m in mode_errors)
            detail = {"R07": f"Deductible {inr(terms.deductible)} per claim",
                      "R08": f"Co-payment {terms.copay_pct:g}%",
                      "R09": f"Remaining sum insured {inr(terms.remaining_si)} of {inr(policy.sum_insured)}"}[fam]
            outcome = "FAIL" if bad else ("NA" if claim.proposed_settlement is None else "PASS")
            checks.append(Check(fam, name, outcome, detail))
        elif fam == "D4":
            hits = [f for f in drafts if f.category == B.NEAR_DUPLICATE]
            checks.append(Check(fam, name, "FAIL" if hits else ("NA" if whole else "PASS"),
                                f"Similar to {hits[0].related_claim_id}" if hits
                                else notes.get("D4", "No similar claims for this member within the window")))
        elif missed:
            checks.append(Check(fam, name, "FAIL", "; ".join(d.title for d in missed), missed[0].clause_ref))
        elif fam_deds:
            checks.append(Check(fam, name, "DEDUCTED",
                                "Correctly deducted in proposed settlement: " + "; ".join(d.title for d in fam_deds),
                                fam_deds[0].clause_ref))
        else:
            note = notes.get(fam, "Passed")
            checks.append(Check(fam, name, "NA" if note.startswith("NA") else "PASS", note))

    assessment = {
        "billed_amount": billed, "expected_settlement": rec.expected, "proposed_settlement": rec.proposed,
        "gap": rec.gap, "reconciliation": rec.summary, "waterfall": rec.waterfall,
        "checks": [c.as_dict() for c in checks], "cleared": cleared, "documents": doc_rows,
    }
    return drafts, assessment


def integrity_findings(claim: Claim, scan: DocumentScan) -> list[FindingDraft]:
    """Document-integrity scan of the claim's source document → suspected findings (MEDIUM/HIGH only)."""
    if scan.level not in ("MEDIUM", "HIGH"):
        return []
    out: list[FindingDraft] = []
    res = scan.results or {}
    conf = round((scan.score or 0) / 100, 2)
    base_ev = {"scan_id": scan.id, "doc_id": scan.doc_id, "fabrication_score": scan.score, "level": scan.level,
               "scan_mode": scan.mode}
    dups = (res.get("find_duplicate_images") or {}).get("duplicates") or []
    if dups:
        d = dups[0]
        est = claim.proposed_settlement if claim.proposed_settlement is not None else claim.requested_amount
        where = d.get("claim_id") or f"document #{d.get('doc_id')}"
        out.append(FindingDraft(
            B.DUPLICATE_DOCUMENT, "F1", f"Bill image reused from {where}", B.SUSPECTED, B.ESTIMATED,
            f"The submitted document matches {d.get('label')} (aligned ink agreement {d['ink_agreement']:.3f}, "
            f"scale {d.get('scale', 1):.2f}). If it is the same bill, the proposed {inr(est)} would pay for it "
            "again (estimate, needs review).",
            clause_ref="Claims procedure Sec 9.4 (one payment per incident)", billed_excess=claim.requested_amount,
            leakage_amount=est, related_claim_id=d.get("claim_id"), confidence=conf,
            evidence=base_ev | {"matched_fingerprint": d.get("fingerprint_id"), "matched_doc_id": d.get("doc_id"),
                                "matched_claim": d.get("claim_id"), "ink_agreement": d["ink_agreement"],
                                "scale": d.get("scale"), "page": d.get("page")}))
    ela_hits = [p for p in res.get("error_level_analysis") or [] if p.get("hotspots")]
    clones = [p for p in res.get("detect_copy_move") or [] if p.get("clusters")]
    if ela_hits or clones:
        parts = []
        if ela_hits:
            parts.append(f"{sum(len(p['hotspots']) for p in ela_hits)} region(s) saved at a different time than the "
                         f"rest of the page (JPEG ghost at quality {ela_hits[0].get('ghost_quality')})")
        if clones:
            c = clones[0]["clusters"][0]
            parts.append(f"a region copied within the page ({c['pairs']} matching keypoints, offset {c['offset']})")
        out.append(FindingDraft(
            B.DOCUMENT_TAMPER, "F2", "Possible edits on the submitted bill", B.SUSPECTED, B.NOT_DETERMINABLE,
            "Image forensics found " + " and ".join(parts) + ". No amount is assumed; verify the original bill.",
            clause_ref="Claims procedure Sec 9.2 (original documents)", confidence=conf,
            evidence=base_ev | {"ela_hotspots": [h | {"page": p["page"]} for p in ela_hits for h in p["hotspots"]],
                                "clone_clusters": [c | {"page": p["page"]} for p in clones for c in p["clusters"]]}))
    return out


def _key(f) -> tuple:
    return (f.claim_id, f.category, f.rule_id, tuple(sorted(f.line_ids or [])), f.related_claim_id)


def run_analysis(session: Session) -> AnalysisRun:
    ctx = Context.load(session)
    index = LineIndex(ctx)
    outliers = build_outliers(ctx)
    scans_by_claim: dict[str, DocumentScan] = {}
    for doc in session.exec(select(Document).where(Document.claim_id.is_not(None))):
        scan = session.exec(select(DocumentScan).where(DocumentScan.doc_id == doc.id, DocumentScan.status == "DONE")
                            .order_by(DocumentScan.id.desc())).first()
        if scan:
            scans_by_claim[doc.claim_id] = scan
    from app.documents.evidence import evidence_by_claim

    ctx.evidence = evidence_by_claim(session)
    image_matches = {cid: {d["claim_id"] for d in (s.results.get("find_duplicate_images") or {}).get("duplicates", [])
                           if d.get("claim_id")} for cid, s in scans_by_claim.items()}
    links = build_links(ctx, image_matches)
    cluster_of = {cid: comp for comp in clusters(links) for cid in comp}
    session.exec(delete(ClaimLink))
    session.add_all(ClaimLink(claim_a=l.a, claim_b=l.b, rule=l.rule, strength=l.strength, attribute=l.attribute,
                              value=l.value, explanation=l.explanation) for l in links)
    run = AnalysisRun()
    session.add(run)
    session.flush()

    # Findings are upserted by key so ids — and the decisions and audit events that reference them — stay stable.
    previous = {_key(f): f for f in session.exec(select(Finding))}
    prev_assess = {a.claim_id: (a.expected_settlement, a.gap) for a in session.exec(select(ClaimAssessment))}
    session.exec(delete(ClaimAssessment))
    computed = ("category", "rule_id", "clause_ref", "title", "finding_type", "amount_basis", "billed_excess",
                "leakage_amount", "calculation", "evidence", "line_ids", "related_claim_id", "confidence")

    count = 0
    seen: set[tuple] = set()
    pending = ctx.pending_claims()
    for claim in pending:
        drafts, a = assess_claim(claim, ctx, index, outliers)
        if claim.claim_id in scans_by_claim:
            drafts.extend(integrity_findings(claim, scans_by_claim[claim.claim_id]))
        drafts.extend(related_findings(claim, ctx, links, cluster_of))
        session.add(ClaimAssessment(claim_id=claim.claim_id, run_id=run.id, **a))
        if prev_assess.get(claim.claim_id) != (a.get("expected_settlement"), a.get("gap")):
            audit.record(session, action="ASSESSED", entity="analysis", entity_id=run.id, claim_id=claim.claim_id,
                         after={k: a.get(k) for k in ("billed_amount", "expected_settlement", "proposed_settlement",
                                                      "gap", "reconciliation")},
                         before=None if claim.claim_id not in prev_assess else
                         dict(zip(("expected_settlement", "gap"), prev_assess[claim.claim_id])))
        for d in drafts:
            f = Finding(run_id=run.id, claim_id=claim.claim_id, severity=severity(d), **d.__dict__)
            key = _key(f)
            seen.add(key)
            old = previous.get(key)
            if old is None:
                session.add(f)
                session.flush()
                audit.record(session, action="FINDING_CREATED", entity="finding", entity_id=f.id,
                             claim_id=claim.claim_id, after={k: getattr(f, k) for k in computed + ("severity",)})
            else:
                changed = {k: getattr(f, k) for k in ("leakage_amount", "billed_excess", "calculation", "severity")
                           if getattr(f, k) != getattr(old, k)}
                for k in computed + ("severity", "run_id"):
                    setattr(old, k, getattr(f, k))
                session.add(old)
                if changed:
                    audit.record(session, action="FINDING_RECALCULATED", entity="finding", entity_id=old.id,
                                 claim_id=claim.claim_id, after=changed)
            count += 1
    for key, old in previous.items():  # no longer reproduced (data corrected, rule changed)
        if key not in seen:
            audit.record(session, action="FINDING_WITHDRAWN", entity="finding", entity_id=old.id, claim_id=old.claim_id,
                         before={k: getattr(old, k) for k in ("category", "title", "leakage_amount", "status")},
                         reason="not reproduced by analysis run")
            session.delete(old)
    session.flush()
    _sync_recommendations(session, ctx, run.id)
    audit.record(session, action="ANALYSIS_RUN", entity="analysis", entity_id=run.id,
                 after={"claims": len(pending), "findings": count})
    run.claims_analyzed = len(pending)
    run.findings_count = count
    run.finished_at = datetime.now(UTC)
    session.commit()
    session.refresh(run)
    return run


def _sync_recommendations(session: Session, ctx: Context, run_id: int) -> None:
    """One live recommendation per confirmed calculated finding. Rows are never edited: a recalculation that
    changes the numbers supersedes the old version, so the original stays in the evidence trail."""
    live = {r.finding_id: r for r in session.exec(select(Recommendation).where(Recommendation.superseded == False))}  # noqa: E712
    findings = {f.id: f for f in session.exec(select(Finding))}
    for fid, f in findings.items():
        draft = recommend(f, ctx.claims[f.claim_id], ctx)
        old = live.pop(fid, None)
        if draft is None:
            if old is not None:
                old.superseded = True
                session.add(old)
            continue
        if old is not None and (old.type, old.original_amount, old.corrected_amount, old.settlement_impact,
                                tuple(old.target_line_ids)) == draft.signature():
            continue
        version = 1
        if old is not None:
            old.superseded = True
            session.add(old)
            version = old.version + 1
        rec = Recommendation(claim_id=f.claim_id, finding_id=fid, version=version, type=draft.type, action=draft.action,
                             target_line_ids=draft.target_line_ids, original_amount=draft.original_amount,
                             corrected_amount=draft.corrected_amount, delta=draft.delta,
                             settlement_impact=draft.settlement_impact, calculation=draft.calculation,
                             rule_id=draft.rule_id, clause_ref=draft.clause_ref, clause_text=draft.clause_text,
                             run_id=run_id)
        session.add(rec)
        session.flush()
        audit.record(session, action="RECOMMENDATION_ISSUED", entity="recommendation", entity_id=rec.id,
                     claim_id=f.claim_id, after={"finding_id": fid, "version": version, "type": rec.type,
                                                 "original": rec.original_amount, "corrected": rec.corrected_amount,
                                                 "impact": rec.settlement_impact})
    for old in live.values():  # finding withdrawn
        old.superseded = True
        session.add(old)
