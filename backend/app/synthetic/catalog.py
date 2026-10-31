"""Reference data for synthetic health claims: service catalogue, plans and policy rules."""

# code: (description, category, base price INR)
SERVICES: dict[str, tuple[str, str, float]] = {
    "ROOM-WRD": ("Room rent - ward / semi-private (per day)", "room", 3000),
    "ICU-STD": ("ICU charges (per day)", "icu", 9000),
    "CON-IP": ("Inpatient doctor visit", "consultation", 800),
    "CON-OP": ("Outpatient consultation", "consultation", 700),
    "CON-SPL": ("Specialist consultation", "consultation", 1200),
    "LAB-CBC": ("Complete blood count", "lab", 400),
    "LAB-CRP": ("C-reactive protein", "lab", 600),
    "LAB-LFT": ("Liver function test", "lab", 900),
    "LAB-KFT": ("Kidney function test", "lab", 900),
    "LAB-HBA1C": ("HbA1c", "lab", 550),
    "LAB-TROP": ("Troponin I", "lab", 1800),
    "LAB-PLT": ("Platelet count", "lab", 250),
    "LAB-DNS1": ("Dengue NS1 antigen", "lab", 1200),
    "RAD-XRC": ("X-ray chest PA view", "radiology", 600),
    "RAD-XRL": ("X-ray limb (2 views)", "radiology", 700),
    "RAD-USG": ("Ultrasound abdomen", "radiology", 1800),
    "RAD-CT": ("CT scan abdomen (contrast)", "radiology", 4500),
    "RAD-MRI": ("MRI scan (single region)", "radiology", 8000),
    "RAD-ECG": ("ECG", "radiology", 300),
    "RAD-ECHO": ("2D echocardiography", "radiology", 2500),
    "CRD-ANG": ("Coronary angiography", "procedure", 18000),
    "PRC-PTCA": ("PTCA with stent", "procedure", 180000),
    "PRC-APP": ("Appendectomy (laparoscopic)", "procedure", 45000),
    "PRC-LCH": ("Laparoscopic cholecystectomy", "procedure", 60000),
    "PRC-ORIF": ("ORIF tibia fracture", "procedure", 55000),
    "PRC-HER": ("Inguinal hernia repair (mesh)", "procedure", 40000),
    "PRC-CAT": ("Cataract surgery - phaco with IOL (per eye)", "procedure", 38000),
    "PRC-COS": ("Rhinoplasty (cosmetic)", "procedure", 65000),
    "OT-CHG": ("Operation theatre charges", "procedure", 12000),
    "ANS-GEN": ("Anaesthesia charges", "procedure", 9000),
    "PHY-SES": ("Physiotherapy session", "physio", 600),
    "DIA-SES": ("Haemodialysis session", "dialysis", 2800),
    "CHM-SES": ("Chemotherapy cycle (day care)", "chemo", 25000),
    "PHA-MED": ("Pharmacy - medicines", "pharmacy", 3000),
    "PHA-IV": ("IV fluids and sets", "pharmacy", 1200),
    "AMB-01": ("Road ambulance", "ambulance", 1500),
    # Non-payable items (IRDAI-style list) — excluded under every plan
    "CNS-GLV": ("Disposable gloves", "consumables", 450),
    "CNS-MSK": ("Face masks", "consumables", 300),
    "CNS-ADM": ("Admission kit", "consumables", 1200),
    "CNS-TOI": ("Toiletries", "consumables", 350),
    "CNS-REG": ("Registration / file charges", "consumables", 500),
}
NON_PAYABLE = ["CNS-GLV", "CNS-MSK", "CNS-ADM", "CNS-TOI", "CNS-REG"]

DIAGNOSES = {
    "K35.80": "Acute appendicitis",
    "K80.20": "Gallstones without cholecystitis",
    "J18.9": "Pneumonia, unspecified",
    "A90": "Dengue fever",
    "I21.9": "Acute myocardial infarction",
    "S82.20": "Fracture of shaft of tibia",
    "K40.90": "Inguinal hernia",
    "H25.9": "Age-related cataract",
    "C50.9": "Malignant neoplasm of breast",
    "N18.6": "End stage renal disease",
    "M54.5": "Low back pain",
    "E11.9": "Type 2 diabetes mellitus",
    "J06.9": "Acute upper respiratory infection",
    "M17.1": "Primary osteoarthritis of knee",
    "Z41.1": "Encounter for cosmetic surgery",
}

PLANS = {
    "SILVER": dict(sum_insured=300000, room_rent_limit=3000, icu_limit=6000, deductible=0, copay_pct=10),
    "GOLD": dict(sum_insured=500000, room_rent_limit=5000, icu_limit=10000, deductible=1000, copay_pct=0),
    "PLATINUM": dict(sum_insured=1000000, room_rent_limit=8000, icu_limit=15000, deductible=2500, copay_pct=0),
}
SENIOR_COPAY = 20


def policy_rules() -> list[dict]:
    rows = []
    for plan in PLANS:
        p = plan[:3]
        cat_limit = 60000 if plan == "PLATINUM" else 40000
        for i, code in enumerate(NON_PAYABLE, 1):
            rows.append(dict(rule_id=f"{p}-EXC-{i:02d}", plan=plan, clause_ref="Sec 7.3 (Non-payable items)",
                             rule_type="EXCLUSION_CODE", target_type="code", target=code, value=None,
                             description=f"{SERVICES[code][0]} are non-payable consumables"))
        rows += [
            dict(rule_id=f"{p}-EXD-01", plan=plan, clause_ref="Sec 7.1 (Cosmetic treatment)",
                 rule_type="EXCLUSION_DIAGNOSIS", target_type="icd_prefix", target="Z41", value=None,
                 description="Cosmetic or aesthetic treatment is excluded"),
            dict(rule_id=f"{p}-SUB-01", plan=plan, clause_ref="Sec 5.2 (Cataract sub-limit)",
                 rule_type="SUBLIMIT_PER_CLAIM", target_type="code", target="PRC-CAT", value=cat_limit,
                 description=f"Cataract surgery limited to ₹{cat_limit:,} per eye"),
            dict(rule_id=f"{p}-SUB-02", plan=plan, clause_ref="Sec 5.4 (Ambulance)",
                 rule_type="SUBLIMIT_PER_CLAIM", target_type="category", target="ambulance", value=2000,
                 description="Road ambulance limited to ₹2,000 per hospitalisation"),
            dict(rule_id=f"{p}-FRQ-01", plan=plan, clause_ref="Sec 5.6 (Physiotherapy)",
                 rule_type="FREQUENCY_CAP", target_type="category", target="physio", value=12,
                 description="Physiotherapy limited to 12 sessions per policy year"),
            dict(rule_id=f"{p}-WAI-01", plan=plan, clause_ref="Sec 6.2 (Specific waiting periods)",
                 rule_type="DISEASE_WAITING", target_type="icd_prefix", target="H25", value=730,
                 description="Cataract: 2-year waiting period"),
            dict(rule_id=f"{p}-WAI-02", plan=plan, clause_ref="Sec 6.2 (Specific waiting periods)",
                 rule_type="DISEASE_WAITING", target_type="icd_prefix", target="K40", value=730,
                 description="Hernia: 2-year waiting period"),
        ]
    return rows


def category_of(code: str) -> str | None:
    s = SERVICES.get(code)
    return s[1] if s else None
