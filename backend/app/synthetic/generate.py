"""Seeded synthetic health-insurance dataset with planted leakage and a ground-truth file.

    uv run python -m app.synthetic.generate --out ../sample_data --seed 42

1. Clean generation: policies, members, providers, ~1,200 paid history claims and ~1,000 pending
   claims built from clinical episode templates. Proposed settlements are computed correctly
   (non-payables and sub-limit excess deducted), so a clean claim carries no leakage.
2. Injection: each scenario mutates or adds claims and records what the handler "missed";
   every injection is written to ground_truth.csv with its expected category and amount.
"""

from __future__ import annotations

import argparse
import copy
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from faker import Faker

from app.engine.settlement import Mode, Terms, finalize
from app.synthetic.catalog import DIAGNOSES, NON_PAYABLE, PLANS, SENIOR_COPAY, SERVICES, policy_rules

PENDING_FROM, PENDING_TO, LAST_SUBMIT = date(2026, 9, 1), date(2026, 10, 25), date(2026, 10, 30)
HISTORY_TO = date(2026, 8, 31)
TIER_MULT = {1: 1.25, 2: 1.0, 3: 0.82}
CITIES = {1: ["Mumbai", "Delhi", "Bengaluru", "Chennai", "Hyderabad", "Pune"],
          2: ["Jaipur", "Lucknow", "Kochi", "Indore", "Nagpur", "Coimbatore"],
          3: ["Nashik", "Mysuru", "Guntur", "Udaipur", "Hubballi"]}
OVERPRICED_CODES = {"RAD-MRI", "RAD-CT"}

EPISODE_WEIGHTS = {"appendicitis": 8, "chole": 6, "pneumonia": 7, "dengue": 6, "mi": 3, "fracture": 6,
                   "hernia": 3, "cataract": 5, "diabetes_op": 12, "urti_op": 12, "knee_op": 7, "back_op": 7,
                   "physio": 6}
LONG_WAIT = {"cataract": 730, "hernia": 730}


@dataclass
class GClaim:
    seq: int
    policy: dict
    member: dict
    provider: dict
    kind: str
    claim_type: str
    diagnosis: str | None
    incident: date
    admission: date | None
    discharge: date | None
    submitted: date
    status: str
    invoice_no: str | None
    lines: list[dict]
    deductions: list[dict] = field(default_factory=list)  # {kind, amount|None(=remainder), missed}
    mode: Mode = field(default_factory=Mode)
    claim_id: str | None = None
    tags: set = field(default_factory=set)
    proposed: float | None = None
    expected: float | None = None
    paid_date: date | None = None
    si_fill_for: GClaim | None = None
    no_proposed: bool = False

    @property
    def billed(self) -> float:
        return round(sum(l["amount"] for l in self.lines), 2)

    @property
    def end(self) -> date:
        return max([self.discharge or self.incident] + [l["service_date"] for l in self.lines])


class Generator:
    def __init__(self, seed: int):
        self.r = random.Random(seed)
        self.fake = Faker("en_IN")
        self.fake.seed_instance(seed)
        self.seq = 0
        self.policies: list[dict] = []
        self.members: list[dict] = []
        self.providers: list[dict] = []
        self.claims: list[GClaim] = []
        self.gt: list[dict] = []
        self.busy: dict[str, list[tuple[date, date]]] = defaultdict(list)
        self.inv_seq: dict[str, int] = {}

    # ------------------------------------------------------------ reference data
    def make_providers(self):
        r = self.r
        specs = [("H", "HOSPITAL", 30), ("D", "DIAGNOSTIC", 5), ("C", "CLINIC", 7)]
        for prefix, ptype, n in specs:
            for i in range(1, n + 1):
                tier = r.choices([1, 2, 3], [4, 4, 2])[0]
                last = self.fake.last_name()
                suffix = {"HOSPITAL": r.choice(["Multispeciality Hospital", "Hospital", "Medical Centre", "Care Hospital"]),
                          "DIAGNOSTIC": r.choice(["Diagnostics", "Imaging & Labs", "Scan Centre"]),
                          "CLINIC": r.choice(["Physio & Rehab Clinic", "Kidney Care Centre", "Wellness Clinic"])}[ptype]
                pid = f"PRV-{prefix}{i:03d}"
                self.providers.append(dict(provider_id=pid, name=f"{last} {suffix}", provider_type=ptype,
                                           city=r.choice(CITIES[tier]), tier=tier, network=r.random() < 0.85,
                                           overpriced=False, prefix="".join(w[0] for w in f"{last} {suffix}".split()[:3]).upper() + pid[-2:]))
                self.inv_seq[pid] = r.randint(1000, 5000)
        for p in self.providers[3:5]:  # two hospitals bill imaging far above peers
            p["overpriced"] = True
        self.hospitals = [p for p in self.providers if p["provider_type"] == "HOSPITAL"]
        self.diagnostics = [p for p in self.providers if p["provider_type"] == "DIAGNOSTIC"]
        self.clinics = [p for p in self.providers if p["provider_type"] == "CLINIC"]

    def make_policies(self, n: int):
        r = self.r
        for i in range(1, n + 1):
            plan = r.choices(list(PLANS), [4, 4, 2])[0]
            terms = PLANS[plan]
            age = r.randint(26, 72)
            start = date(2025, 11, 1) + timedelta(days=r.randint(0, 272))
            end = start.replace(year=start.year + 1) - timedelta(days=1)
            inception = start if r.random() < 0.3 else start - timedelta(days=365 * r.randint(1, 6))
            holder = self.fake.name()
            pol = dict(policy_no=f"HP{start.year}{i:05d}", holder_name=holder, plan=plan,
                       sum_insured=terms["sum_insured"], inception_date=inception, start_date=start, end_date=end,
                       status="ACTIVE", deductible=terms["deductible"],
                       copay_pct=SENIOR_COPAY if age >= 60 else terms["copay_pct"],
                       room_rent_limit=terms["room_rent_limit"], icu_limit=terms["icu_limit"],
                       initial_waiting_days=30, _age=age,
                       _home=r.choice(self.hospitals[:5] * 2 + self.hospitals))  # overpriced hospitals get extra share
            self.policies.append(pol)
            people = [("SELF", holder, age)]
            if r.random() < 0.7:
                people.append(("SPOUSE", self.fake.name(), max(22, age + r.randint(-5, 3))))
            if age < 55:
                for _ in range(r.choice([0, 1, 1, 2])):
                    people.append(("CHILD", self.fake.name(), r.randint(1, 22)))
            if age < 48 and r.random() < 0.2:
                people.append(("PARENT", self.fake.name(), age + r.randint(24, 32)))
            for k, (rel, name, a) in enumerate(people, 1):
                enrolled = inception if (rel != "CHILD" or r.random() > 0.15) else start
                self.members.append(dict(member_id=f"{pol['policy_no']}-{k:02d}", policy_no=pol["policy_no"],
                                         name=name, relationship=rel, gender=r.choice(["M", "F"]),
                                         dob=date(2026 - a, r.randint(1, 12), r.randint(1, 28)),
                                         enrolled_date=enrolled, _policy=pol, _age=a))

    # ------------------------------------------------------------ helpers
    def price(self, code: str, prov: dict) -> float:
        base = SERVICES[code][2] * TIER_MULT[prov["tier"]] * self.r.lognormvariate(0, 0.07)
        if prov["overpriced"] and code in OVERPRICED_CODES:
            base *= 2.6
        return float(round(base, -1))

    def line(self, code: str, d: date, prov: dict, qty: float = 1, price: float | None = None) -> dict:
        desc, cat, _ = SERVICES[code]
        p = float(price if price is not None else self.price(code, prov))
        return dict(service_date=d, service_code=code, description=desc, category=cat, quantity=float(qty),
                    unit_price=p, amount=round(p * qty, 2), provider_id=prov["provider_id"])

    def invoice(self, prov: dict, d: date) -> str:
        self.inv_seq[prov["provider_id"]] += 1
        return f"{prov['prefix']}/INV/{d.year}/{self.inv_seq[prov['provider_id']]:06d}"

    def cover_from(self, m: dict) -> date:
        return max(m["_policy"]["inception_date"], m["enrolled_date"])

    def reserve(self, mid: str, lo: date, hi: date, gap: int = 7) -> bool:
        for a, b in self.busy[mid]:
            if lo <= b + timedelta(days=gap) and hi >= a - timedelta(days=gap):
                return False
        self.busy[mid].append((lo, hi))
        return True

    def pick_date(self, m: dict, lo: date, hi: date, length: int, gap: int = 7) -> date | None:
        if hi < lo:
            return None
        for _ in range(25):
            d = lo + timedelta(days=self.r.randint(0, (hi - lo).days))
            if self.reserve(m["member_id"], d, d + timedelta(days=length), gap):
                return d
        return None

    def provider_for(self, kind: str, m: dict) -> dict:
        r = self.r
        if kind == "physio" or kind == "dialysis":
            return r.choice(self.clinics)
        if kind in ("knee_op", "back_op") and r.random() < 0.4:
            return r.choice(self.diagnostics)
        return m["_policy"]["_home"] if r.random() < 0.7 else r.choice(self.hospitals)

    # ------------------------------------------------------------ episodes
    def episode(self, kind: str, m: dict, prov: dict, d: date, status: str) -> GClaim:
        r, pol = self.r, m["_policy"]
        L = lambda code, day=0, qty=1, price=None: self.line(code, d + timedelta(days=day), prov, qty, price)  # noqa: E731
        room = lambda days, day=0: L("ROOM-WRD", day, days, max(1500.0, pol["room_rent_limit"] - r.choice([0, 0, 500, 1000])))  # noqa: E731
        icu = lambda days, day=0: L("ICU-STD", day, days, min(pol["icu_limit"], float(round(9000 * TIER_MULT[prov["tier"]], -2))))  # noqa: E731
        pharm = lambda day, lo, hi: L("PHA-MED", day, 1, float(r.randint(lo, hi) // 10 * 10))  # noqa: E731
        lines: list[dict] = []
        ctype, diag, stay = "INPATIENT", None, 0
        surgical = {"appendicitis": ("K35.80", "PRC-APP", 3), "chole": ("K80.20", "PRC-LCH", 3),
                    "fracture": ("S82.20", "PRC-ORIF", 3), "hernia": ("K40.90", "PRC-HER", 2),
                    "cosmetic": ("Z41.1", "PRC-COS", 1)}
        if kind in surgical:
            diag, proc, stay = surgical[kind]
            lines += [room(stay)] + [L("CON-IP", k) for k in range(stay)]
            lines += [L("LAB-CBC", 0), L(proc, min(1, stay - 1)), L("OT-CHG", min(1, stay - 1)),
                      L("ANS-GEN", min(1, stay - 1)), pharm(stay - 1, 3000, 9000)]
            if kind == "appendicitis":
                lines += [L("RAD-USG", 0), L("PHA-IV", 0), L("LAB-CBC", 2)]
                if r.random() < 0.35:
                    lines.append(L("RAD-CT", 0))
            if kind == "chole":
                lines += [L("RAD-USG", 0), L("LAB-LFT", 0)]
            if kind == "fracture":
                lines += [L("RAD-XRL", 0), L("RAD-XRL", 2)]
                if r.random() < 0.4:
                    lines.append(L("AMB-01", 0, 1, min(2000.0, self.price("AMB-01", prov))))
        elif kind == "pneumonia":
            diag, stay = "J18.9", 5
            icu_days = r.choice([0, 0, 1, 2])
            if icu_days:
                lines.append(icu(icu_days))
            lines += [room(stay - icu_days, icu_days)] + [L("CON-IP", k) for k in range(stay)]
            lines += [L("LAB-CBC", 0), L("LAB-CBC", 2), L("LAB-CBC", 4), L("LAB-CRP", 0), L("RAD-XRC", 0),
                      L("RAD-XRC", 3), L("PHA-IV", 0), pharm(4, 4000, 12000)]
        elif kind == "dengue":
            diag, stay = "A90", 4
            lines += [room(stay)] + [L("CON-IP", k) for k in range(stay)] + [L("LAB-PLT", k) for k in range(stay)]
            lines += [L("LAB-DNS1", 0), L("LAB-CBC", 0), L("PHA-IV", 0), pharm(3, 2000, 6000)]
        elif kind == "mi":
            diag, stay = "I21.9", 5
            lines += [icu(2), room(3, 2)] + [L("CON-IP", k) for k in range(stay)]
            lines += [L("LAB-TROP", 0), L("RAD-ECG", 0), L("RAD-ECG", 1), L("RAD-ECHO", 1), L("CRD-ANG", 1),
                      pharm(4, 6000, 15000)]
            if r.random() < 0.25:
                lines.append(L("PRC-PTCA", 1))
        elif kind == "cataract":
            ctype, diag = "DAYCARE", "H25.9"
            lines += [L("CON-SPL", 0), L("PRC-CAT", 0), L("LAB-CBC", 0), pharm(0, 800, 2000)]
        elif kind == "chemo":
            ctype, diag = "DAYCARE", "C50.9"
            lines += [L("CHM-SES", 0), L("LAB-CBC", 0), pharm(0, 3000, 8000)]
        elif kind == "dialysis":
            ctype, diag = "DIALYSIS", "N18.6"
            lines.append(L("DIA-SES", 0))
            if r.random() < 0.3:
                lines.append(L("LAB-KFT", 0))
        elif kind == "physio":
            ctype, diag = "PHYSIO", "M54.5"
            n = r.randint(4, 6)
            p = self.price("PHY-SES", prov)
            lines += [L("PHY-SES", 2 * k, 1, p) for k in range(n)]
            stay = 2 * (n - 1)
        else:  # outpatient
            ctype = "OUTPATIENT"
            if kind == "diabetes_op":
                diag = "E11.9"
                lines += [L("CON-OP"), L("LAB-HBA1C"), L("LAB-KFT"), pharm(0, 1000, 3000)]
            elif kind == "urti_op":
                diag = "J06.9"
                lines += [L("CON-OP"), pharm(0, 500, 1800)]
            elif kind == "knee_op":
                diag = "M17.1"
                lines += [L("CON-SPL"), L("RAD-XRL")] + ([L("RAD-MRI")] if r.random() < 0.6 else [])
            else:
                diag = "M54.5"
                lines += [L("CON-SPL")] + ([L("RAD-MRI")] if r.random() < 0.75 else [])

        if ctype == "INPATIENT" and kind != "cosmetic" and r.random() < 0.4:
            for code in r.sample(NON_PAYABLE, r.choice([1, 2])):
                lines.append(L(code, 0))
        adm = d if ctype in ("INPATIENT", "DAYCARE") else None
        dis = d + timedelta(days=stay) if adm else None
        end = max(l["service_date"] for l in lines)
        submit_lo = end + timedelta(days=2)
        submitted = submit_lo + timedelta(days=r.randint(0, 10))
        if status == "PENDING":
            submitted = min(submitted, LAST_SUBMIT)
        self.seq += 1
        c = GClaim(self.seq, pol, m, prov, kind, ctype, diag, d, adm, dis, submitted, status,
                   self.invoice(prov, end), lines)
        for ln in lines:
            if ln["service_code"] in NON_PAYABLE:
                c.deductions.append(dict(kind="non_payable", amount=ln["amount"], missed=False, line=ln))
        self.cataract_deduction(c)
        if status == "PAID":
            c.paid_date = submitted + timedelta(days=r.randint(5, 20))
        self.claims.append(c)
        return c

    def cataract_deduction(self, c: GClaim):
        c.deductions = [d for d in c.deductions if d["kind"] != "sublimit_cataract"]
        cat = [l for l in c.lines if l["service_code"] == "PRC-CAT"]
        if cat:
            limit = 60000 if c.policy["plan"] == "PLATINUM" else 40000
            excess = round(sum(l["amount"] for l in cat) - limit, 2)
            if excess > 0:
                c.deductions.append(dict(kind="sublimit_cataract", amount=excess, missed=False))

    def new_claim(self, kind: str, m: dict, status: str, lo: date, hi: date, gap: int = 7) -> GClaim | None:
        length = {"physio": 10}.get(kind, 6)
        cover = self.cover_from(m)
        lo = max(lo, m["_policy"]["start_date"], cover + timedelta(days=LONG_WAIT.get(kind, 31)))
        d = self.pick_date(m, lo, hi - timedelta(days=length), length, gap)
        if d is None:
            return None
        return self.episode(kind, m, self.provider_for(kind, m), d, status)

    # ------------------------------------------------------------ clean generation
    def generate_clean(self, n_history: int, n_pending: int):
        r = self.r
        kinds, weights = list(EPISODE_WEIGHTS), list(EPISODE_WEIGHTS.values())
        physio_sessions: dict[str, int] = defaultdict(int)

        def one(status, lo, hi):
            for _ in range(10):
                kind = r.choices(kinds, weights)[0]
                m = r.choice(self.members)
                if kind == "physio" and physio_sessions[m["member_id"]] > 6:
                    continue
                c = self.new_claim(kind, m, status, lo, hi)
                if c:
                    if kind == "physio":
                        physio_sessions[m["member_id"]] += len(c.lines)
                    return c
            return None

        for _ in range(n_history):
            one("PAID", date(2025, 11, 1), HISTORY_TO)
        for _ in range(n_pending):
            one("PENDING", PENDING_FROM, PENDING_TO)

        # recurring treatment series — the legitimate look-alikes
        series_members = r.sample(self.members, 22)
        for i, m in enumerate(series_members):
            kind, step = ("dialysis", r.choice([3, 4])) if i < 14 else ("chemo", 21)
            prov = r.choice(self.clinics if kind == "dialysis" else self.hospitals)
            earliest = max(m["_policy"]["start_date"], self.cover_from(m) + timedelta(days=31))
            for status, lo, hi in (("PAID", max(HISTORY_TO - timedelta(days=60), earliest), HISTORY_TO),
                                   ("PENDING", max(PENDING_FROM, earliest), PENDING_TO)):
                d = lo + timedelta(days=r.randint(0, 6))
                while d <= hi:
                    if self.reserve(m["member_id"], d, d, gap=0):
                        c = self.episode(kind, m, prov, d, status)
                        c.tags.add("series")
                    d += timedelta(days=step + r.choice([0, 0, 1]))

    # ------------------------------------------------------------ injection
    def pick(self, pred, n: int, tag: str) -> list[GClaim]:
        pool = [c for c in self.claims if c.status == "PENDING" and not c.tags and pred(c)]
        chosen = self.r.sample(pool, min(n, len(pool)))
        for c in chosen:
            c.tags.add(tag)
        return chosen

    def add_gt(self, c: GClaim, scenario: str, category: str, ftype: str = "CONFIRMED_VIOLATION", note: str = ""):
        self.gt.append(dict(claim=c, scenario=scenario, expected_category=category, expected_finding_type=ftype,
                            note=note))

    def miss(self, c: GClaim, kind: str, amount: float, line: dict | None = None):
        c.deductions.append(dict(kind=kind, amount=round(amount, 2), missed=True, line=line))

    def clone_claim(self, src: GClaim, shift: int = 0) -> GClaim:
        c = copy.copy(src)
        self.seq += 1
        c.seq, c.status, c.tags, c.claim_id, c.mode = self.seq, "PENDING", set(), None, Mode()
        c.lines = [dict(l, service_date=l["service_date"] + timedelta(days=shift)) for l in src.lines]
        c.deductions = [dict(d, missed=False) for d in src.deductions]
        for attr in ("incident", "admission", "discharge"):
            v = getattr(src, attr)
            setattr(c, attr, v + timedelta(days=shift) if v else None)
        c.submitted = min(max(src.submitted + timedelta(days=self.r.randint(3, 20)),
                              c.end + timedelta(days=2)), LAST_SUBMIT)
        c.paid_date = None
        self.claims.append(c)
        return c

    def has(self, c: GClaim, *, cat: str | None = None, code: str | None = None) -> bool:
        return any((cat is None or l["category"] == cat) and (code is None or l["service_code"] == code)
                   for l in c.lines)

    def inject(self):
        r = self.r
        inpatient = lambda c: c.claim_type == "INPATIENT" and self.has(c, cat="room")  # noqa: E731

        # Hero claim from the problem statement
        hero_m = next(m for m in self.members if m["relationship"] == "SELF" and m["_policy"]["plan"] == "GOLD"
                      and m["_policy"]["_age"] >= 60 and m["_policy"]["start_date"] <= date(2026, 9, 1)
                      and self.reserve(m["member_id"], date(2026, 10, 6), date(2026, 10, 9), gap=10))
        hero_pol = hero_m["_policy"]
        hero = self.episode("appendicitis", hero_m, hero_pol["_home"], date(2026, 10, 6), "PENDING")
        hero.lines = [l for l in hero.lines if l["service_code"] not in NON_PAYABLE]
        hero.deductions = []
        room = next(l for l in hero.lines if l["category"] == "room")
        room.update(unit_price=7000.0, amount=7000.0 * room["quantity"])
        self.miss(hero, "room", (7000 - hero_pol["room_rent_limit"]) * room["quantity"], room)
        cbc = next(l for l in hero.lines if l["service_code"] == "LAB-CBC")
        dup = dict(cbc)
        hero.lines.append(dup)
        self.miss(hero, "dup_line", dup["amount"], dup)
        glv = self.line("CNS-GLV", hero.incident, hero.provider)
        hero.lines.append(glv)
        self.miss(hero, "non_payable", glv["amount"], glv)
        hero.mode = Mode("copay_not_applied", copay_pct=0)
        hero.claim_id, hero.tags = "CLM-DEMO-01", {"hero"}
        for cat in ("DUPLICATE_LINE", "ROOM_RENT_LIMIT", "EXCLUDED_SERVICE", "SETTLEMENT_MISCALC"):
            self.add_gt(hero, "17_combined_hero", cat, note="Problem-statement example")

        # 1 exact duplicate claims (resubmission of paid or pending claims)
        src = r.sample([c for c in self.claims if not c.tags and c.kind not in ("dialysis", "chemo", "physio")], 15)
        for s in src:
            c = self.clone_claim(s)
            c.tags.add("dup_claim")
            s.tags.add("dup_source")
            c.deductions.append(dict(kind="duplicate_claim", amount=None, missed=True))
            self.add_gt(c, "01_exact_duplicate_claim", "DUPLICATE_CLAIM", note=f"copy of seq {s.seq}")

        # 2 duplicate line within claim
        for c in self.pick(lambda c: self.has(c, cat="lab") or self.has(c, cat="radiology"), 25, "dup_line"):
            ln = r.choice([l for l in c.lines if l["category"] in ("lab", "radiology")])
            d = dict(ln)
            c.lines.append(d)
            self.miss(c, "dup_line", d["amount"], d)
            self.add_gt(c, "02_duplicate_line_within", "DUPLICATE_LINE")

        # 3 line already billed on an earlier (paid) claim of the same member and provider
        paid_by = defaultdict(list)
        for c in self.claims:
            if c.status == "PAID" and not c.tags:
                paid_by[(c.member["member_id"], c.provider["provider_id"])].append(c)
        cands = [c for c in self.claims if c.status == "PENDING" and not c.tags and c.kind not in ("dialysis", "chemo")
                 and any(l["category"] in ("lab", "radiology") for h in paid_by[(c.member["member_id"], c.provider["provider_id"])] for l in h.lines)]
        for c in r.sample(cands, min(15, len(cands))):
            c.tags.add("cross_line")
            h = r.choice([h for h in paid_by[(c.member["member_id"], c.provider["provider_id"])]
                          if any(l["category"] in ("lab", "radiology") for l in h.lines)])
            d = dict(r.choice([l for l in h.lines if l["category"] in ("lab", "radiology")]))
            c.lines.append(d)
            self.miss(c, "cross_line", d["amount"], d)
            self.add_gt(c, "03_duplicate_line_across", "DUPLICATE_LINE")

        # 4 near-duplicate resubmissions (dates ±1-2 days, small amount change, altered invoice ref)
        src = r.sample([c for c in self.claims if not c.tags and c.claim_type in ("INPATIENT", "DAYCARE")
                        and c.kind not in ("chemo",)], 15)
        for s in src:
            c = self.clone_claim(s, shift=r.choice([-2, -1, 1, 2]))
            s.tags.add("near_source")
            c.tags.add("near_dup")
            digits = s.invoice_no.rsplit("/", 1)
            if r.random() < 0.5:
                c.invoice_no = f"{digits[0]}/{digits[1].lstrip('0')}"
            else:
                c.invoice_no = f"{digits[0]}/{digits[1][:-2]}{digits[1][-1]}{digits[1][-2]}"
                if c.invoice_no == s.invoice_no:
                    c.invoice_no = f"{digits[0]}/{int(digits[1]) + 1:06d}"
            for l in c.lines:
                if l["category"] == "pharmacy":
                    l["unit_price"] = l["amount"] = float(round(l["amount"] * r.uniform(0.94, 1.06), -1))
            self.add_gt(c, "04_near_duplicate", "NEAR_DUPLICATE", "SUSPECTED_PATTERN")

        # 5 room rent above daily limit / 6 ICU above limit
        for c in self.pick(lambda c: inpatient(c) and c.kind != "cosmetic", 30, "room"):
            ln = next(l for l in c.lines if l["category"] == "room")
            rate = c.policy["room_rent_limit"] + r.choice([1000, 1500, 2000, 2500, 3000])
            ln.update(unit_price=float(rate), amount=float(rate * ln["quantity"]))
            self.miss(c, "room", (rate - c.policy["room_rent_limit"]) * ln["quantity"], ln)
            self.add_gt(c, "05_room_rent_limit", "ROOM_RENT_LIMIT")
        for c in self.pick(lambda c: self.has(c, cat="icu"), 10, "icu"):
            ln = next(l for l in c.lines if l["category"] == "icu")
            rate = c.policy["icu_limit"] + r.choice([2000, 3000, 4000])
            ln.update(unit_price=float(rate), amount=float(rate * ln["quantity"]))
            self.miss(c, "icu", (rate - c.policy["icu_limit"]) * ln["quantity"], ln)
            self.add_gt(c, "06_icu_limit", "ICU_LIMIT")

        # 7 non-payable consumables paid
        for c in self.pick(lambda c: c.claim_type == "INPATIENT" and not self.has(c, cat="consumables"), 30, "consumables"):
            for code in r.sample(NON_PAYABLE, r.randint(1, 3)):
                ln = self.line(code, c.incident, c.provider)
                c.lines.append(ln)
                self.miss(c, "non_payable", ln["amount"], ln)
            self.add_gt(c, "07_excluded_consumables", "EXCLUDED_SERVICE")

        # 8 excluded diagnosis (cosmetic), 10 waiting periods — new claims, paid in full
        for _ in range(8):
            for _ in range(20):
                c = self.new_claim("cosmetic", r.choice(self.members), "PENDING", PENDING_FROM, PENDING_TO)
                if c:
                    c.tags.add("cosmetic")
                    c.deductions.append(dict(kind="excluded_diagnosis", amount=None, missed=True))
                    self.add_gt(c, "08_excluded_diagnosis", "EXCLUDED_DIAGNOSIS")
                    break
        young = [m for m in self.members if (PENDING_TO - self.cover_from(m)).days < 700]
        made = 0
        for m in r.sample(young, len(young)):
            if made >= 6:
                break
            kind = r.choice(["cataract", "hernia"])
            d = self.pick_date(m, max(PENDING_FROM, self.cover_from(m) + timedelta(days=40)), PENDING_TO - timedelta(days=4), 4)
            if d and (d - self.cover_from(m)).days < 730:
                c = self.episode(kind, m, self.provider_for(kind, m), d, "PENDING")
                c.tags.add("waiting")
                c.deductions.append(dict(kind="waiting", amount=None, missed=True))
                self.add_gt(c, "10_waiting_period", "WAITING_PERIOD", note="disease-specific 2-year waiting")
                made += 1
        new_pols = [p for p in self.policies if p["inception_date"] == p["start_date"] and p["start_date"] >= date(2026, 6, 1)]
        for p in r.sample(new_pols, min(2, len(new_pols))):
            m = next(m for m in self.members if m["policy_no"] == p["policy_no"])
            d = p["start_date"] + timedelta(days=r.randint(8, 20))
            self.busy[m["member_id"]] = [iv for iv in self.busy[m["member_id"]] if iv[0] > d + timedelta(days=12)]
            self.reserve(m["member_id"], d, d + timedelta(days=5), gap=0)
            c = self.episode(r.choice(["pneumonia", "dengue"]), m, p["_home"], d, "PENDING")
            c.tags.add("waiting")
            c.deductions.append(dict(kind="waiting", amount=None, missed=True))
            self.add_gt(c, "10_waiting_period", "WAITING_PERIOD", note="initial 30-day waiting")

        # 9 policy lapsed before the incident (policies with a single pending claim)
        pend_by_pol = defaultdict(list)
        for c in self.claims:
            if c.status == "PENDING":
                pend_by_pol[c.policy["policy_no"]].append(c)
        lapsable = [v[0] for v in pend_by_pol.values() if len(v) == 1 and not v[0].tags and v[0].incident > date(2026, 9, 10)
                    and all(h.incident < v[0].incident - timedelta(days=45) for h in self.claims
                            if h.policy is v[0].policy and h.status == "PAID")]
        for c in r.sample(lapsable, 8):
            c.tags.add("lapsed")
            c.policy["status"] = "LAPSED"
            c.policy["end_date"] = c.incident - timedelta(days=r.randint(5, 40))
            c.deductions.append(dict(kind="coverage", amount=None, missed=True))
            self.add_gt(c, "09_coverage_lapsed", "COVERAGE")

        # 11 sub-limits: cataract, ambulance, physio frequency cap
        for c in self.pick(lambda c: c.kind == "cataract", 5, "sub_cataract"):
            ln = next(l for l in c.lines if l["service_code"] == "PRC-CAT")
            limit = 60000 if c.policy["plan"] == "PLATINUM" else 40000
            price = float(limit + r.randint(8, 15) * 1000)
            ln.update(unit_price=price, amount=price)
            c.deductions = [d for d in c.deductions if d["kind"] != "sublimit_cataract"]
            self.miss(c, "sublimit_cataract", price - limit, ln)
            self.add_gt(c, "11_sub_limit", "SUB_LIMIT", note="cataract")
        for c in self.pick(lambda c: inpatient(c) and not self.has(c, cat="ambulance"), 5, "sub_amb"):
            ln = self.line("AMB-01", c.incident, c.provider, 1, float(r.choice([3200, 3500, 4000])))
            c.lines.append(ln)
            self.miss(c, "sublimit_ambulance", ln["amount"] - 2000, ln)
            self.add_gt(c, "11_sub_limit", "SUB_LIMIT", note="ambulance")
        physio_members = {c.member["member_id"] for c in self.claims if c.kind == "physio"}
        made = 0
        for m in r.sample(self.members, len(self.members)):
            if made >= 5 or m["member_id"] in physio_members:
                continue
            pol = m["_policy"]
            if pol["status"] != "ACTIVE" or (date(2026, 8, 1) - max(pol["start_date"], self.cover_from(m))).days < 60:
                continue
            prov = r.choice(self.clinics)
            d1 = self.pick_date(m, max(pol["start_date"], self.cover_from(m) + timedelta(days=31)), date(2026, 8, 1) - timedelta(days=20), 20)
            d2 = self.pick_date(m, PENDING_FROM, PENDING_TO - timedelta(days=12), 12)
            if not (d1 and d2):
                continue
            p = self.price("PHY-SES", prov)
            h = self.episode("physio", m, prov, d1, "PAID")
            h.lines = [self.line("PHY-SES", d1 + timedelta(days=2 * k), prov, 1, p) for k in range(10)]
            h.submitted = h.lines[-1]["service_date"] + timedelta(days=3)
            h.paid_date = h.submitted + timedelta(days=7)
            h.tags.add("physio_hist")
            c = self.episode("physio", m, prov, d2, "PENDING")
            c.lines = [self.line("PHY-SES", d2 + timedelta(days=2 * k), prov, 1, p) for k in range(6)]
            c.submitted = min(c.lines[-1]["service_date"] + timedelta(days=3), LAST_SUBMIT)
            c.tags.add("physio_cap")
            self.miss(c, "frequency_cap", 4 * p)
            self.add_gt(c, "11_sub_limit", "FREQUENCY_CAP", note="10 prior + 6 sessions vs cap 12")
            made += 1

        # 12 settlement calculation errors
        for c in self.pick(lambda c: c.policy["deductible"] > 0 and c.billed > c.policy["deductible"] * 2, 7, "s_ded"):
            c.mode = Mode("deductible_not_applied", skip_deductible=True)
            self.add_gt(c, "12_settlement_miscalc", "SETTLEMENT_MISCALC", note="deductible not applied")
        for c in self.pick(lambda c: c.policy["copay_pct"] > 0, 7, "s_copay"):
            c.mode = Mode("copay_not_applied", copay_pct=0)
            self.add_gt(c, "12_settlement_miscalc", "SETTLEMENT_MISCALC", note="co-pay not applied")
        for c in self.pick(lambda c: c.policy["copay_pct"] == 20, 6, "s_rate"):
            c.mode = Mode("copay_wrong_rate_10", copay_pct=10)
            self.add_gt(c, "12_settlement_miscalc", "SETTLEMENT_MISCALC", note="co-pay 10% instead of 20%")
        single = [v[0] for v in pend_by_pol.values() if len(v) == 1 and not v[0].tags and v[0].billed > 30000]
        for c in r.sample(single, min(5, len(single))):
            m = c.member
            d = self.pick_date(m, max(c.policy["start_date"], HISTORY_TO - timedelta(days=40)), HISTORY_TO - timedelta(days=6), 6, gap=0)
            if not d:
                continue
            c.tags.add("s_si")
            h = self.episode("mi", m, c.policy["_home"], d, "PAID")
            h.tags.add("si_fill")
            h.si_fill_for = c
            c.mode = Mode("si_cap_not_applied", skip_cap=True)
            self.add_gt(c, "12_settlement_miscalc", "SETTLEMENT_MISCALC", note="sum insured exceeded")

        # 13 invoice arithmetic error (line amount ≠ qty × rate)
        for c in self.pick(lambda c: inpatient(c), 10, "arith"):
            ln = next(l for l in c.lines if l["category"] == "room")
            ln["amount"] = round(ln["unit_price"] * (ln["quantity"] + 1), 2)
            self.miss(c, "arithmetic", ln["unit_price"], ln)
            self.add_gt(c, "13_arithmetic_error", "ARITHMETIC_ERROR")

        # 17 combined multi-issue claims
        for c in self.pick(lambda c: inpatient(c) and not self.has(c, cat="consumables"), 9, "combined"):
            ln = next(l for l in c.lines if l["category"] == "room")
            rate = c.policy["room_rent_limit"] + 2000
            ln.update(unit_price=float(rate), amount=float(rate * ln["quantity"]))
            self.miss(c, "room", 2000 * ln["quantity"], ln)
            g = self.line("CNS-ADM", c.incident, c.provider)
            c.lines.append(g)
            self.miss(c, "non_payable", g["amount"], g)
            if c.policy["copay_pct"] > 0:
                c.mode = Mode("copay_not_applied", copay_pct=0)
            else:
                c.mode = Mode("deductible_not_applied", skip_deductible=True)
            for cat in ("ROOM_RENT_LIMIT", "EXCLUDED_SERVICE", "SETTLEMENT_MISCALC"):
                self.add_gt(c, "17_combined", cat)

        # 16 incomplete evidence (otherwise clean)
        for i, c in enumerate(self.pick(lambda c: c.claim_type == "INPATIENT", 15, "incomplete")):
            if i < 5:
                c.diagnosis = None
                note = "missing diagnosis"
            elif i < 10:
                c.discharge = None
                note = "missing discharge date"
            else:
                c.invoice_no = None
                note = "missing invoice number"
            self.add_gt(c, "16_incomplete_evidence", "INCOMPLETE_EVIDENCE", "NEEDS_INFO", note)

    # ------------------------------------------------------------ settlement + ids
    def finalize(self):
        r = self.r
        order = sorted(self.claims, key=lambda c: (c.status != "PAID", c.submitted, c.seq))
        n = 0
        for c in order:
            if not c.claim_id:
                n += 1
                c.claim_id = f"CLM-{n:06d}"
            inv = c.invoice_no
            for k, l in enumerate(c.lines, 1):
                l["line_id"] = f"{c.claim_id}-L{k:02d}"
                l["claim_id"] = c.claim_id
                l["invoice_no"] = inv

        for pol in self.policies:
            pcl = [c for c in self.claims if c.policy is pol]
            paid_so_far = 0.0
            for c in sorted((c for c in pcl if c.status == "PAID"), key=lambda c: (c.incident, c.seq)):
                remaining = pol["sum_insured"] - paid_so_far
                terms = Terms(pol["deductible"], pol["copay_pct"], remaining)
                adm = c.billed - sum(d["amount"] for d in c.deductions)
                c.proposed = finalize(adm, terms)
                if c.si_fill_for is not None:
                    t = c.si_fill_for
                    adm_t = t.billed - sum(d["amount"] or 0 for d in t.deductions)
                    want_left = round(finalize(adm_t, Terms(pol["deductible"], pol["copay_pct"], 1e12)) * 0.4, -2)
                    target = max(0.0, round(remaining - want_left, 2))
                    need_adm = target / (1 - pol["copay_pct"] / 100) + pol["deductible"]
                    if adm < need_adm:  # hospital bill large enough to justify the payment
                        big = next(l for l in c.lines if l["service_code"] in ("PRC-PTCA", "CRD-ANG"))
                        big["unit_price"] = big["amount"] = float(round(big["amount"] + need_adm - adm + 1000, -2))
                        adm = c.billed - sum(d["amount"] for d in c.deductions)
                    c.proposed = min(finalize(adm, terms), target)
                if pol["start_date"] <= c.incident <= pol["end_date"]:
                    paid_so_far += c.proposed
            remaining = pol["sum_insured"] - sum(c.proposed for c in pcl if c.status == "PAID"
                                                 and pol["start_date"] <= c.incident <= pol["end_date"])
            terms = Terms(pol["deductible"], pol["copay_pct"], remaining)
            for c in (c for c in pcl if c.status == "PENDING"):
                known = sum(d["amount"] for d in c.deductions if d["amount"] is not None)
                for d in c.deductions:
                    if d["amount"] is None:  # whole-claim: everything not otherwise deducted
                        d["amount"] = round(c.billed - known, 2)
                applied = sum(d["amount"] for d in c.deductions if not d["missed"])
                total = sum(d["amount"] for d in c.deductions)
                c.proposed = finalize(c.billed - applied, terms, c.mode)
                c.expected = finalize(c.billed - total, terms)

        # 14 provider outliers & 15 legitimate look-alikes are labelled after the fact
        for c in self.claims:
            if c.status != "PENDING":
                continue
            if c.provider["overpriced"] and any(l["service_code"] in OVERPRICED_CODES for l in c.lines):
                self.add_gt(c, "14_provider_outlier", "PROVIDER_OUTLIER", "SUSPECTED_PATTERN")
            if not c.tags - {"series"} and (c.kind in ("dialysis", "chemo", "physio", "dengue")):
                self.add_gt(c, "15_legitimate_lookalike", "NONE", "NONE",
                            note=f"recurring {c.kind}: must not be flagged")
        _ = r

    # ------------------------------------------------------------ output
    def frames(self) -> dict[str, pd.DataFrame]:
        strip = lambda d: {k: v for k, v in d.items() if not k.startswith("_") and k not in ("overpriced", "prefix")}  # noqa: E731
        claims, lines, settlements = [], [], []
        for c in sorted(self.claims, key=lambda c: c.claim_id):
            claims.append(dict(
                claim_id=c.claim_id, policy_no=c.policy["policy_no"], member_id=c.member["member_id"],
                claimant_name=c.member["name"], claim_type=c.claim_type, incident_date=c.incident,
                admission_date=c.admission, discharge_date=c.discharge, diagnosis_code=c.diagnosis,
                provider_id=c.provider["provider_id"], invoice_no=c.invoice_no, requested_amount=c.billed,
                proposed_settlement=None if c.no_proposed else c.proposed, submitted_date=c.submitted,
                status=c.status))
            for l in c.lines:
                lines.append({k: l[k] for k in ("line_id", "claim_id", "invoice_no", "provider_id", "service_date",
                                                "service_code", "description", "category", "quantity",
                                                "unit_price", "amount")})
            if c.status == "PAID":
                settlements.append(dict(settlement_id=f"STL-{c.claim_id[4:]}", claim_id=c.claim_id,
                                        approved_amount=c.proposed, paid_amount=c.proposed, paid_date=c.paid_date,
                                        payment_ref=f"NEFT{self.r.randint(10**9, 10**10 - 1)}"))
        gt = [dict(claim_id=g["claim"].claim_id, scenario=g["scenario"], expected_category=g["expected_category"],
                   expected_finding_type=g["expected_finding_type"],
                   expected_claim_leakage=round((g["claim"].proposed or 0) - (g["claim"].expected or 0), 2)
                   if g["expected_finding_type"] == "CONFIRMED_VIOLATION" else None,
                   note=g["note"]) for g in self.gt]
        # An injected error that does not change the payable amount (e.g. sum insured already exhausted)
        # is not leakage — keep it out of the ground truth.
        self.no_impact = [g for g in gt if g["expected_claim_leakage"] is not None and g["expected_claim_leakage"] <= 1]
        gt = [g for g in gt if g not in self.no_impact]
        return {
            "policies": pd.DataFrame([strip(p) for p in self.policies]),
            "policy_rules": pd.DataFrame(policy_rules()),
            "members": pd.DataFrame([strip(m) for m in self.members]),
            "providers": pd.DataFrame([strip(p) for p in self.providers]),
            "claims": pd.DataFrame(claims),
            "invoice_lines": pd.DataFrame(lines),
            "settlements": pd.DataFrame(settlements),
            "ground_truth": pd.DataFrame(gt).sort_values(["scenario", "claim_id"]),
        }


def dirty_claims(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Rows that ingestion must reject (or warn on) — never silently drop."""
    pol = frames["policies"].iloc[0]
    mem = frames["members"][frames["members"].policy_no == pol.policy_no].iloc[0]
    prov = frames["providers"].iloc[0].provider_id
    base = dict(policy_no=pol.policy_no, member_id=mem.member_id, claimant_name=mem["name"], claim_type="OUTPATIENT",
                incident_date="2026-10-12", admission_date="", discharge_date="", diagnosis_code="J06.9",
                provider_id=prov, invoice_no="", requested_amount=1800, proposed_settlement=1620,
                submitted_date="2026-10-20", status="PENDING")
    rows = []
    for i in range(1, 6):
        rows.append(base | dict(claim_id=f"CLM-NEW{i:03d}", invoice_no=f"WALKIN/{i:04d}"))
    bad = [
        dict(claim_id="", note="missing claim id"),
        dict(claim_id="CLM-BAD001", incident_date="31-02-2026", note="impossible date"),
        dict(claim_id="CLM-BAD002", incident_date="2026/13/05", note="invalid month"),
        dict(claim_id="CLM-BAD003", requested_amount=-4500, note="negative amount"),
        dict(claim_id="CLM-BAD004", requested_amount="12,0O0", note="non-numeric amount"),
        dict(claim_id="CLM-BAD005", policy_no="HP2099XXXXX", note="unknown policy"),
        dict(claim_id="CLM-BAD006", member_id="HP2099XXXXX-01", note="unknown member"),
        dict(claim_id="CLM-BAD007", claim_type="INPATIENT", admission_date="2026-10-10", discharge_date="2026-10-07",
             note="discharge before admission"),
        dict(claim_id="CLM-NEW001", note="duplicate id within file"),
        dict(claim_id="CLM-000010", note="id already loaded"),
        dict(claim_id="CLM-BAD008", claim_type="", note="missing claim type"),
        dict(claim_id="CLM-BAD009", claim_type="DENTALX", note="invalid claim type"),
        dict(claim_id="CLM-BAD010", incident_date="", note="missing incident date"),
        dict(claim_id="CLM-BAD011", provider_id="", note="missing provider"),
        dict(claim_id="CLM-BAD012", provider_id="PRV-Z999", note="unknown provider"),
        dict(claim_id="CLM-BAD013", incident_date="2027-01-15", submitted_date="2027-01-20", note="future date"),
        dict(claim_id="CLM-BAD014", requested_amount="", note="missing amount"),
        dict(claim_id="CLM-BAD015", submitted_date="2026-10-01", note="submitted before incident"),
        dict(claim_id="CLM BAD 016", note="malformed id"),
        dict(claim_id="CLM-WARN01", proposed_settlement=2500, note="proposed above requested (warning, accepted)"),
    ]
    for b in bad:
        note = b.pop("note")
        rows.append(base | b | {"_expected": note})
    df = pd.DataFrame(rows)
    df["_expected"] = df["_expected"].fillna("valid")
    return df.rename(columns={"_expected": "test_note"})


def write(frames: dict[str, pd.DataFrame], out: Path, gen: Generator):
    out.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        df = df.copy()
        for col in df.columns:
            if df[col].map(lambda v: isinstance(v, date)).any():
                df[col] = df[col].map(lambda v: v.isoformat() if isinstance(v, date) else v)
        df.to_csv(out / f"{name}.csv", index=False)
        if name != "ground_truth":
            df.to_json(out / f"{name}.json", orient="records", indent=1, force_ascii=False)
    with pd.ExcelWriter(out / "claims_bundle.xlsx") as xw:
        for name in ("policies", "policy_rules", "members", "providers", "claims", "invoice_lines", "settlements"):
            pd.read_csv(out / f"{name}.csv").to_excel(xw, sheet_name=name, index=False)
    dirty_claims(frames).to_csv(out / "claims_dirty.csv", index=False)
    from app.synthetic.documents import write_documents

    write_documents(gen, out / "documents")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[3] / "sample_data")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--policies", type=int, default=300)
    ap.add_argument("--history", type=int, default=1200)
    ap.add_argument("--pending", type=int, default=820)
    a = ap.parse_args()
    g = Generator(a.seed)
    g.make_providers()
    g.make_policies(a.policies)
    g.generate_clean(a.history, a.pending)
    g.inject()
    g.finalize()
    frames = g.frames()
    write(frames, a.out, g)
    gt = frames["ground_truth"]
    print(f"policies={len(frames['policies'])} members={len(frames['members'])} claims={len(frames['claims'])} "
          f"(pending={int((frames['claims'].status == 'PENDING').sum())}) lines={len(frames['invoice_lines'])}")
    print(gt.groupby("scenario").size().to_string())
    print(f"injections without payable impact (excluded from ground truth): {len(g.no_impact)}")
    print(f"written to {a.out}")


if __name__ == "__main__":
    main()
