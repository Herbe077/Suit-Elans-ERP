"""Seguridad: hash bcrypt directo + JWT HS256 + cookie HTTPOnly."""
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

ALGORITHM = "HS256"
COOKIE_NAME = "suitelans_token"


def hash_password(plain: str) -> str:
    if not isinstance(plain, str) or not plain:
        raise ValueError("La contraseña no puede estar vacía")
    raw = plain.encode()
    if len(raw) > 72:
        raise ValueError("La contraseña excede el límite de 72 bytes de bcrypt")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        if not isinstance(plain, str) or not isinstance(hashed, str):
            return False
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


def create_access_token(sub: str, role: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": sub, "role": role, "exp": exp}, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
