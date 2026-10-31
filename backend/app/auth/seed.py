"""Demo accounts: one per staff role, and policyholders mapped to real policy holders in the loaded data."""

from sqlmodel import Session, select

from app.auth.permissions import DEMO_PASSWORD, STAFF_DEMO_USERS
from app.auth.security import hash_password
from app.models import Claim, Member, User

# Policyholders worth demoing: the problem-statement claim, a reused-bill history, plus ordinary claimants.
DEMO_CLAIMS = ["CLM-DEMO-01", "CLM-000048"]


def seed_staff(session: Session) -> int:
    pw = hash_password(DEMO_PASSWORD)
    n = 0
    for username, name, role in STAFF_DEMO_USERS:
        if not session.exec(select(User).where(User.username == username)).first():
            session.add(User(username=username, full_name=name, role=role, password_hash=pw))
            n += 1
    session.commit()
    return n


def seed_policyholders(session: Session, extra: int = 3) -> int:
    pw = hash_password(DEMO_PASSWORD)
    policies: list[str] = []
    for cid in DEMO_CLAIMS:
        c = session.get(Claim, cid)
        if c and c.policy_no not in policies:
            policies.append(c.policy_no)
    for c in session.exec(select(Claim).where(Claim.status == "PENDING").order_by(Claim.claim_id)):
        if len(policies) >= len(DEMO_CLAIMS) + extra:
            break
        if c.policy_no not in policies:
            policies.append(c.policy_no)
    n = 0
    for pol in policies:
        holder = session.exec(select(Member).where(Member.policy_no == pol, Member.relationship == "SELF")).first()
        if not holder:
            continue
        username = holder.name.split()[0].lower() + pol[-3:]
        if session.exec(select(User).where(User.username == username)).first():
            continue
        session.add(User(username=username, full_name=holder.name, role="POLICYHOLDER", password_hash=pw,
                         member_id=holder.member_id, policy_no=pol))
        n += 1
    session.commit()
    return n
