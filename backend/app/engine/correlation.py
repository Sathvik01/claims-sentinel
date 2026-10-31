"""Cross-claim correlation: links between claims through shared invoices, accounts, contacts, incidents and
document images, explained attribute by attribute. Strong links across policies, or clusters spanning three or
more policies, become RELATED_CLAIMS findings — for review, never a fraud determination."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations

import networkx as nx

from app.engine import base as B
from app.engine.base import Context, FindingDraft, inr, norm_invoice, norm_invoice_loose
from app.models import Claim

STRONG, MEDIUM, WEAK = "STRONG", "MEDIUM", "WEAK"
HIGH_VALUE_LINE = 5000
RING_MIN_POLICIES = 3
MAX_GROUP = 12  # an attribute shared by more claims than this is too common to be a link (e.g. a hospital switchboard)


@dataclass
class Link:
    a: str
    b: str
    rule: str
    strength: str
    attribute: str
    value: str
    explanation: str


def _pairs(groups: dict, rule: str, strength: str, attribute: str, ctx: Context, *, cross_policy: bool,
           text: str) -> list[Link]:
    out = []
    for value, ids in groups.items():
        ids = sorted(set(ids))
        if len(ids) < 2 or len(ids) > MAX_GROUP:
            continue
        for a, b in combinations(ids, 2):
            ca, cb = ctx.claims[a], ctx.claims[b]
            if cross_policy and ca.policy_no == cb.policy_no:
                continue
            if ca.status == "PAID" and cb.status == "PAID":
                continue
            out.append(Link(a, b, rule, strength, attribute, str(value),
                            text.format(value=value, a=a, b=b, pa=ca.policy_no, pb=cb.policy_no)))
    return out


def build_links(ctx: Context, image_matches: dict[str, set[str]] | None = None) -> list[Link]:
    by_invoice, by_account, by_phone, by_address = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    by_incident, by_line, by_loose = defaultdict(list), defaultdict(list), defaultdict(list)
    for c in ctx.claims.values():
        inv = norm_invoice(c.invoice_no)
        if inv:
            by_invoice[(c.provider_id, c.invoice_no)].append(c.claim_id)
            by_loose[(c.provider_id, norm_invoice_loose(c.invoice_no))].append(c.claim_id)
        if c.payout_account:
            by_account[c.payout_account].append(c.claim_id)
        m = ctx.members.get(c.member_id)
        if m and m.phone:
            by_phone[m.phone].append(c.claim_id)
        if m and m.address:
            by_address[m.address].append(c.claim_id)
        if c.diagnosis_code:
            by_incident[(c.member_id, c.incident_date, c.diagnosis_code)].append(c.claim_id)
        for ln in ctx.lines_by_claim.get(c.claim_id, []):
            if ln.category in ("procedure", "radiology") and ln.amount >= HIGH_VALUE_LINE:
                by_line[(ln.provider_id, ln.service_code, ln.service_date, ln.amount)].append(c.claim_id)

    links = []
    links += _pairs(by_invoice, "X1", STRONG, "invoice number", ctx, cross_policy=True,
                    text="Provider invoice {value[1]} appears on {a} (policy {pa}) and {b} (policy {pb})")
    links += _pairs(by_account, "X2", STRONG, "payout account", ctx, cross_policy=True,
                    text="Settlement for {a} (policy {pa}) and {b} (policy {pb}) goes to the same account {value}")
    links += _pairs(by_incident, "X4", STRONG, "incident", ctx, cross_policy=False,
                    text="{a} and {b} describe the same incident (member, date, diagnosis)")
    links += _pairs(by_phone, "X5", MEDIUM, "phone", ctx, cross_policy=True,
                    text="Claimants on {a} and {b} share phone {value}")
    links += _pairs(by_address, "X6", MEDIUM, "address", ctx, cross_policy=True,
                    text="Claimants on {a} and {b} share address {value}")
    line_groups = {k: v for k, v in by_line.items()
                   if len({ctx.claims[i].member_id for i in v}) > 1}
    links += _pairs(line_groups, "X7", MEDIUM, "billed service", ctx, cross_policy=False,
                    text="Same {value[1]} on {value[2]} for {value[3]} billed by the same provider on {a} and {b}")
    exact = {(l.a, l.b) for l in links if l.rule == "X1"}
    loose = [l for l in _pairs(by_loose, "X8", WEAK, "similar invoice number", ctx, cross_policy=False,
                               text="Invoice numbers on {a} and {b} differ only in formatting ({value[1]})")
             if (l.a, l.b) not in exact and ctx.claims[l.a].member_id != ctx.claims[l.b].member_id]
    links += loose
    for cid, others in (image_matches or {}).items():
        for o in others:
            if o in ctx.claims and o != cid:
                a, b = sorted((cid, o))
                links.append(Link(a, b, "X3", STRONG, "document image", "same bill image",
                                  f"The bill submitted for {cid} is an image of the bill on {o}"))
    return links


def clusters(links: list[Link]) -> list[set[str]]:
    g = nx.Graph()
    for l in links:
        if l.strength in (STRONG, MEDIUM):
            g.add_edge(l.a, l.b)
    return [c for c in nx.connected_components(g) if len(c) > 1]


def related_findings(claim: Claim, ctx: Context, links: list[Link], cluster_of: dict[str, set[str]]) -> list[FindingDraft]:
    mine = [l for l in links if claim.claim_id in (l.a, l.b)]
    strong = [l for l in mine if l.strength == STRONG and l.rule in ("X1", "X2", "X3")]
    cluster = cluster_of.get(claim.claim_id, set())
    policies = {ctx.claims[c].policy_no for c in cluster if c in ctx.claims}
    if not strong and len(policies) < RING_MIN_POLICIES:
        return []
    others = sorted({l.b if l.a == claim.claim_id else l.a for l in mine if l.strength != WEAK})
    reasons = [l.explanation for l in (strong or [l for l in mine if l.strength == MEDIUM])][:4]
    xpol_invoice = [l for l in strong if l.rule == "X1"]
    est = None
    if xpol_invoice and claim.proposed_settlement is not None:
        partner = ctx.claims[xpol_invoice[0].b if xpol_invoice[0].a == claim.claim_id else xpol_invoice[0].a]
        if (partner.submitted_date, partner.claim_id) < (claim.submitted_date, claim.claim_id):
            est = claim.proposed_settlement  # the later claim re-bills an invoice already presented
    ring = len(policies) >= RING_MIN_POLICIES
    title = (f"Linked to {len(others)} claim(s) on other policies" if not ring
             else f"Part of a cluster of {len(cluster)} claims across {len(policies)} policies")
    return [FindingDraft(
        B.RELATED_CLAIMS, "X-RING" if ring else strong[0].rule, title, B.SUSPECTED,
        B.ESTIMATED if est is not None else B.NOT_DETERMINABLE,
        " ".join(reasons) + (f" If the invoice was already presented, the proposed {inr(est)} would pay it twice "
                             "(estimate)." if est is not None else " No amount is assumed; review the linked claims."),
        clause_ref="Claims procedure Sec 9.4 / related-party review", leakage_amount=est,
        billed_excess=claim.requested_amount if est is not None else None,
        related_claim_id=others[0] if others else None, confidence=0.9 if strong else 0.6,
        evidence={"links": [l.__dict__ for l in mine], "cluster": sorted(cluster), "cluster_policies": sorted(policies)},
    )]
