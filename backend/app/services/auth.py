from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from app.config import get_settings


@dataclass(frozen=True)
class AuthenticatedUser:
    username: str
    authenticated: bool = True


def authenticate_credentials(username: str, password: str) -> AuthenticatedUser | None:
    settings = get_settings()
    if not settings.auth_enabled:
        return AuthenticatedUser(username=username or "development", authenticated=False)
    if not _auth_is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication is enabled but AUTH_PASSWORD or AUTH_PASSWORD_HASH is not configured",
        )
    if not secrets.compare_digest(str(username or ""), settings.auth_username):
        return None
    if not _verify_password(password):
        return None
    return AuthenticatedUser(username=settings.auth_username)


def create_session_token(user: AuthenticatedUser) -> str:
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": user.username,
        "iat": now,
        "exp": now + max(60, int(settings.auth_session_ttl_seconds or 28800)),
    }
    encoded_payload = _b64encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"))
    signature = _sign(encoded_payload)
    return f"{encoded_payload}.{signature}"


def verify_session_token(token: str | None) -> AuthenticatedUser | None:
    settings = get_settings()
    if not token or "." not in token:
        return None
    encoded_payload, signature = token.rsplit(".", 1)
    expected_signature = _sign(encoded_payload)
    if not secrets.compare_digest(signature, expected_signature):
        return None
    try:
        payload = json.loads(_b64decode(encoded_payload).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    username = str(payload.get("sub") or "")
    if not username:
        return None
    if settings.auth_enabled and not secrets.compare_digest(username, settings.auth_username):
        return None
    return AuthenticatedUser(username=username)


async def require_authenticated_user(request: Request) -> AuthenticatedUser:
    settings = get_settings()
    if not settings.auth_enabled:
        return AuthenticatedUser(username="development", authenticated=False)

    token = request.cookies.get(settings.auth_session_cookie_name)
    auth_header = request.headers.get("Authorization", "")
    if not token and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    user = verify_session_token(token)
    if user:
        return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


def session_cookie_kwargs() -> dict[str, Any]:
    settings = get_settings()
    return {
        "key": settings.auth_session_cookie_name,
        "httponly": True,
        "secure": bool(settings.auth_cookie_secure),
        "samesite": settings.auth_cookie_samesite,
        "path": "/",
        "max_age": max(60, int(settings.auth_session_ttl_seconds or 28800)),
    }


def expired_session_cookie_kwargs() -> dict[str, Any]:
    settings = get_settings()
    return {
        "key": settings.auth_session_cookie_name,
        "httponly": True,
        "secure": bool(settings.auth_cookie_secure),
        "samesite": settings.auth_cookie_samesite,
        "path": "/",
    }


def _auth_is_configured() -> bool:
    settings = get_settings()
    return bool(settings.auth_password or settings.auth_password_hash)


def _verify_password(password: str) -> bool:
    settings = get_settings()
    if settings.auth_password_hash:
        return _verify_password_hash(password, settings.auth_password_hash)
    return secrets.compare_digest(str(password or ""), str(settings.auth_password or ""))


def _verify_password_hash(password: str, password_hash: str) -> bool:
    parts = str(password_hash or "").split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AUTH_PASSWORD_HASH must use pbkdf2_sha256$iterations$salt$hash",
        )
    _, iterations_raw, salt, expected = parts
    try:
        iterations = int(iterations_raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AUTH_PASSWORD_HASH has invalid iteration count",
        ) from exc
    digest = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt.encode("utf-8"), iterations)
    actual = _b64encode(digest)
    return secrets.compare_digest(actual, expected)


def _sign(encoded_payload: str) -> str:
    settings = get_settings()
    key = str(settings.secret_key or "change-me-to-random-string").encode("utf-8")
    return _b64encode(hmac.new(key, encoded_payload.encode("utf-8"), hashlib.sha256).digest())


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))

