"""Password hashing and JWTs, following FastAPI's OAuth2 + JWT tutorial (pwdlib + PyJWT)."""

from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.config import settings

ALGORITHM = "HS256"
_hasher = PasswordHash.recommended()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _hasher.verify(password, hashed)


def create_token(username: str, role: str) -> str:
    exp = datetime.now(UTC) + timedelta(minutes=settings.token_minutes)
    return jwt.encode({"sub": username, "role": role, "exp": exp}, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
