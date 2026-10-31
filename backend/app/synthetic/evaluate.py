"""Scores the engine's findings against sample_data/ground_truth.csv.

    uv run python -m app.synthetic.evaluate
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pandas as pd
from sqlmodel import Session, select

from app.config import settings
from app.models import Claim, Finding


def evaluate(session: Session, gt_path: Path | None = None) -> dict:
    gt_path = gt_path or settings.sample_data_dir / "ground_truth.csv"
    if not gt_path.exists():
        return {"available": False, "reason": f"{gt_path.name} not found"}
    gt = pd.read_csv(gt_path)
    pending = set(session.exec(select(Claim.claim_id).where(Claim.status == "PENDING")))
    gt = gt[gt.claim_id.isin(pending)]
    no_impact = set(gt[gt.expected_finding_type == "NO_PAYABLE_IMPACT"].claim_id)
    gt = gt[gt.expected_finding_type != "NO_PAYABLE_IMPACT"]
    if gt.empty:
        return {"available": False, "reason": "ground-truth claims are not loaded"}

    by_claim: dict[str, list[Finding]] = defaultdict(list)
    for f in session.exec(select(Finding)):
        by_claim[f.claim_id].append(f)

    def calculated(cid: str) -> float:
        return round(sum(f.leakage_amount or 0 for f in by_claim[cid]
                         if f.amount_basis == "CALCULATED" and f.status != "DISMISSED"), 2)

    scenarios = []
    for scenario, grp in gt.groupby("scenario", sort=True):
        hits, amt_ok, amt_n, misses = 0, 0, 0, []
        for row in grp.itertuples():
            fs = by_claim.get(row.claim_id, [])
            if row.expected_category == "NONE":
                hit = not fs
            else:
                hit = any(f.category == row.expected_category for f in fs)
            hits += hit
            if not hit and len(misses) < 5:
                misses.append(row.claim_id)
            if row.expected_finding_type == "CONFIRMED_VIOLATION" and not pd.isna(row.expected_claim_leakage):
                amt_n += 1
                amt_ok += abs(calculated(row.claim_id) - row.expected_claim_leakage) <= settings.amount_tolerance
        scenarios.append(dict(scenario=scenario, cases=len(grp), detected=hits,
                              recall=round(hits / len(grp), 3), amount_checked=amt_n,
                              amount_exact=amt_ok, sample_misses=misses,
                              metric="no false flag" if scenario.startswith("15") else "detected"))

    planted = set(gt[gt.expected_category != "NONE"].claim_id)
    anomalous = planted | no_impact
    legit = set(gt[gt.expected_category == "NONE"].claim_id)
    flagged = {cid for cid, fs in by_claim.items() if fs}
    unexpected = sorted(flagged - anomalous)
    true_flags = len(flagged & anomalous)
    expected_total = gt[gt.expected_finding_type == "CONFIRMED_VIOLATION"].drop_duplicates("claim_id") \
        .expected_claim_leakage.sum()
    found_total = sum(calculated(cid) for cid in planted)
    return {
        "available": True,
        "scenarios": scenarios,
        "planted_claims": len(planted),
        "flagged_claims": len(flagged),
        "precision": round(true_flags / len(flagged), 3) if flagged else None,
        "recall": round(len(flagged & planted) / len(planted), 3) if planted else None,
        "false_flags_on_legit": len(flagged & legit),
        "unexpected_flags": len(unexpected),
        "unexpected_sample": unexpected[:10],
        "expected_calculated_leakage": round(float(expected_total), 2),
        "detected_calculated_leakage": round(found_total, 2),
    }


if __name__ == "__main__":
    import json

    from app.db import engine

    with Session(engine) as s:
        print(json.dumps(evaluate(s), indent=2, default=str))
