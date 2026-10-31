"""Unusual provider billing: a provider's typical price for a service vs its peers (robust z-score)."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

from app.config import settings
from app.engine.base import ESTIMATED, PROVIDER_OUTLIER, SUSPECTED, Context, FindingDraft, inr
from app.models import Claim

PRICED_CATEGORIES = {"lab", "radiology", "procedure"}
MIN_PEERS = 5


@dataclass
class Outlier:
    provider_id: str
    code: str
    provider_median: float
    peer_median: float
    peer_p75: float
    z: float
    peers: int
    samples: int


def build_outliers(ctx: Context) -> dict[tuple[str, str], Outlier]:
    prices: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for lines in ctx.lines_by_claim.values():
        for ln in lines:
            if ln.category in PRICED_CATEGORIES and ln.unit_price > 0:
                prices[ln.service_code][ln.provider_id].append(ln.unit_price)
    out: dict[tuple[str, str], Outlier] = {}
    for code, by_provider in prices.items():
        medians = {p: statistics.median(v) for p, v in by_provider.items()}
        if len(medians) < MIN_PEERS:
            continue
        for pid, med in medians.items():
            peers = sorted(m for p, m in medians.items() if p != pid)
            peer_med = statistics.median(peers)
            mad = statistics.median(abs(m - peer_med) for m in peers) or peer_med * 0.05
            z = (med - peer_med) / (1.4826 * mad)
            if z > settings.outlier_z and med >= settings.outlier_min_ratio * peer_med:
                p75 = statistics.quantiles(peers, n=4)[2]
                out[(pid, code)] = Outlier(pid, code, med, peer_med, p75, round(z, 1), len(peers),
                                           len(by_provider[pid]))
    return out


def provider_outlier_findings(claim: Claim, ctx: Context, outliers: dict) -> list[FindingDraft]:
    out = []
    for ln in ctx.lines_by_claim.get(claim.claim_id, []):
        o: Outlier | None = outliers.get((ln.provider_id, ln.service_code))
        if not o or ln.unit_price <= o.peer_p75:
            continue
        est = round((ln.unit_price - o.peer_p75) * ln.quantity, 2)
        prov = ctx.providers.get(ln.provider_id)
        out.append(FindingDraft(
            category=PROVIDER_OUTLIER, rule_id="P1", title=f"{ln.description} priced far above peers",
            finding_type=SUSPECTED, amount_basis=ESTIMATED, billed_excess=est, leakage_amount=est,
            line_ids=[ln.line_id], confidence=min(0.95, 0.5 + o.z / 40),
            calculation=(f"{prov.name if prov else ln.provider_id} typically bills {inr(o.provider_median)} for "
                         f"{ln.service_code} vs peer median {inr(o.peer_median)} across {o.peers} providers "
                         f"(robust z = {o.z}). Estimated excess vs peer 75th percentile: "
                         f"({inr(ln.unit_price)} − {inr(o.peer_p75)}) × {ln.quantity:g} = {inr(est)}. "
                         "Flagged for review — not a fraud determination."),
            evidence={"provider_id": ln.provider_id, "service_code": ln.service_code, "unit_price": ln.unit_price,
                      "provider_median": o.provider_median, "peer_median": o.peer_median,
                      "peer_p75": o.peer_p75, "robust_z": o.z, "peer_count": o.peers,
                      "provider_samples": o.samples},
        ))
    return out
