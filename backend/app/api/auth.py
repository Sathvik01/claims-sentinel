from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import Session, select

from app.auth.deps import CurrentUser
from app.auth.permissions import AUTHORITY, DEMO_PASSWORD, PERMISSIONS, ROLES
from app.auth.security import create_token, verify_password
from app.config import settings
from app.db import get_session
from app.models import Notification, User

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/auth/token")
def login(form: Annotated[OAuth2PasswordRequestForm, Depends()], session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == form.username)).first()
    if not user or not user.active or not verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password",
                            headers={"WWW-Authenticate": "Bearer"})
    return {"access_token": create_token(user.username, user.role), "token_type": "bearer"}


def profile(user: User) -> dict:
    return {"username": user.username, "full_name": user.full_name, "role": user.role,
            "role_label": ROLES.get(user.role, user.role), "permissions": sorted(PERMISSIONS.get(user.role, set())),
            "authority_limit": AUTHORITY.get(user.role, 0) if user.role in AUTHORITY else 0,
            "member_id": user.member_id, "policy_no": user.policy_no}


@router.get("/auth/me")
def me(user: CurrentUser):
    return profile(user)


@router.get("/auth/demo-accounts")
def demo_accounts(session: Session = Depends(get_session)):
    """Login-page picker (demo builds only)."""
    if not settings.demo_mode:
        raise HTTPException(404)
    users = session.exec(select(User).where(User.active)).all()
    order = list(ROLES)
    return {"password": DEMO_PASSWORD,
            "accounts": sorted(({"username": u.username, "full_name": u.full_name, "role": u.role,
                                 "role_label": ROLES[u.role], "policy_no": u.policy_no} for u in users),
                               key=lambda a: (order.index(a["role"]), a["username"]))}


@router.get("/notifications")
def my_notifications(user: CurrentUser, session: Session = Depends(get_session)):
    q = select(Notification).where((Notification.username == user.username) | (Notification.role == user.role))
    rows = sorted(session.exec(q).all(), key=lambda n: n.id, reverse=True)[:50]
    return {"unread": sum(not n.read for n in rows), "items": rows}


@router.post("/notifications/read")
def mark_read(user: CurrentUser, session: Session = Depends(get_session)):
    for n in session.exec(select(Notification).where(Notification.username == user.username, Notification.read == False)):  # noqa: E712
        n.read = True
        session.add(n)
    session.commit()
    return {"ok": True}
