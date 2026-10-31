"""FastAPI dependencies: the signed-in user, and `require(permission)` guards for routes."""

from collections.abc import Callable
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session, select

from app.auth.permissions import has
from app.auth.security import decode_token
from app.db import get_session
from app.models import Claim, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


def current_user(header_token: Annotated[str | None, Depends(oauth2_scheme)], access_token: str | None = None,
                 session: Session = Depends(get_session)) -> User:
    """Bearer header, or `?access_token=` for <img>/download links that cannot send headers."""
    token = header_token or access_token
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in or session expired",
                                 headers={"WWW-Authenticate": "Bearer"})
    if not token:
        raise unauthorized
    try:
        payload = decode_token(token)
    except jwt.InvalidTokenError as e:
        raise unauthorized from e
    user = session.exec(select(User).where(User.username == payload.get("sub"))).first()
    if user is None or not user.active:
        raise unauthorized
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require(*permissions: str) -> Callable[[User], User]:
    """Route dependency: the user must hold every listed permission."""

    def check(user: CurrentUser) -> User:
        missing = [p for p in permissions if not has(user.role, p)]
        if missing:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your role ({user.role}) lacks: {', '.join(missing)}")
        return user

    return check


def own_claim_or_403(user: User, claim: Claim) -> None:
    """Policyholders may only touch claims on their own policy."""
    if user.role == "POLICYHOLDER" and claim.policy_no != user.policy_no:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Claim not found")
