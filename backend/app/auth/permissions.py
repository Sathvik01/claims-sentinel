"""Roles, the permissions each one holds, and approval authority (one table, enforced by `require`)."""

ROLES = {
    "POLICYHOLDER": "Policyholder",
    "HANDLER": "Claims Handler",
    "ANALYST": "Claims Analyst",
    "SENIOR": "Senior Claims Reviewer",
    "SIU": "SIU Investigator",
    "ADMIN": "Claims Ops Admin",
}

_STAFF_READ = {"claims:view", "findings:view", "documents:view", "notifications:view"}

PERMISSIONS: dict[str, set[str]] = {
    "POLICYHOLDER": {"portal:access", "claim:file_own", "claim:view_own", "evidence:respond_own", "notifications:view"},
    "HANDLER": _STAFF_READ | {"claim:triage", "claim:approve_clean", "evidence:request", "documents:manage"},
    "ANALYST": _STAFF_READ | {"finding:decide", "evidence:request", "escalation:create", "note:write", "claim:decide",
                              "report:export", "documents:manage"},
    "SENIOR": _STAFF_READ | {"finding:decide", "evidence:request", "escalation:create", "escalation:resolve",
                             "approval:resolve", "note:write", "claim:decide", "claim:assign", "siu:view",
                             "report:export", "documents:manage", "quality:view", "audit:view"},
    "SIU": _STAFF_READ | {"siu:view", "siu:outcome", "escalation:resolve", "note:write", "claim:hold", "report:export"},
    "ADMIN": _STAFF_READ | {"admin:users", "data:ingest", "analysis:run", "audit:view", "quality:view", "report:export",
                            "documents:manage"},
}

# Highest settlement a role may approve on its own (None = unlimited). Others cannot approve at all.
AUTHORITY: dict[str, float | None] = {"HANDLER": 50_000, "ANALYST": 200_000, "SENIOR": None}
OVERRIDE_REVIEW_THRESHOLD = 10_000  # analyst overrides of a recommendation beyond this need senior approval

DEMO_PASSWORD = "demo1234"
STAFF_DEMO_USERS = [
    ("admin", "Asha Menon", "ADMIN"),
    ("handler", "Rohit Kulkarni", "HANDLER"),
    ("analyst", "Neha Iyer", "ANALYST"),
    ("analyst2", "Vikram Shah", "ANALYST"),
    ("senior", "Farah Qureshi", "SENIOR"),
    ("siu", "Devendra Rao", "SIU"),
]


def has(role: str, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, set())
