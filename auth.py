# auth.py — operator + viewer authentication
#
# Multi-user session auth using bcrypt + signed cookies.
# Credentials stored in .env.
#   Operator (Brad) — full control: approve/reject trades, toggle modes
#   Viewer (Shane)  — read-only: can see everything but can't change anything

import logging
import os

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from fastapi import HTTPException, Request, Response

log = logging.getLogger(__name__)

# ── User registry ────────────────────────────────────────────────────────────
# Each entry: { username: { hash: ..., role: "operator"|"viewer" } }
_USERS: dict[str, dict] = {}

_op_user = os.getenv("OPERATOR_USERNAME", "")
_op_hash = os.getenv("OPERATOR_PASSWORD_HASH", "")
if _op_user and _op_hash:
    _USERS[_op_user] = {"hash": _op_hash, "role": "operator"}

_viewer_user = os.getenv("VIEWER_USERNAME", "")
_viewer_hash = os.getenv("VIEWER_PASSWORD_HASH", "")
if _viewer_user and _viewer_hash:
    _USERS[_viewer_user] = {"hash": _viewer_hash, "role": "viewer"}

_admin_user = os.getenv("ADMIN_USERNAME", "")
_admin_hash = os.getenv("ADMIN_PASSWORD_HASH", "")
if _admin_user and _admin_hash:
    _USERS[_admin_user] = {"hash": _admin_hash, "role": "operator"}

OPERATOR_USERNAME = _op_user or "operator"

SESSION_SECRET = os.getenv("SESSION_SECRET", "")
if not SESSION_SECRET or len(SESSION_SECRET) < 32:
    raise ValueError(
        "SESSION_SECRET must be set in .env and be at least 32 characters. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
    )
SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE_HOURS", "24")) * 3600
COOKIE_NAME = "densewealth_session"
SECURE_COOKIES = os.getenv("SECURE_COOKIES", "").lower() in ("true", "1", "yes")

_signer = TimestampSigner(SESSION_SECRET)


def authenticate(username: str, plain_password: str) -> str | None:
    """Verify credentials. Returns role ('operator'/'viewer') or None."""
    user = _USERS.get(username)
    if not user:
        return None
    try:
        if bcrypt.checkpw(plain_password.encode("utf-8"), user["hash"].encode("utf-8")):
            return user["role"]
    except Exception:
        return None
    return None


def verify_password(plain_password: str) -> bool:
    """Legacy: check against operator hash only."""
    if not _op_hash:
        return False
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        _op_hash.encode("utf-8"),
    )


def create_session(response: Response, username: str, role: str) -> None:
    """Set signed session cookie on response. Encodes username:role."""
    payload = f"{username}:{role}"
    token = _signer.sign(payload).decode("utf-8")
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=SECURE_COOKIES,
        samesite="strict",
    )


def get_current_user(request: Request) -> dict | None:
    """Extract and validate session. Returns {'username': ..., 'role': ...} or None."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        payload = _signer.unsign(token, max_age=SESSION_MAX_AGE).decode("utf-8")
        if ":" in payload:
            username, role = payload.split(":", 1)
        else:
            # Legacy token (pre-role) — treat as operator
            username, role = payload, "operator"
        return {"username": username, "role": role}
    except (BadSignature, SignatureExpired):
        return None


def require_auth(request: Request) -> dict:
    """FastAPI dependency — raises 401 for API routes, redirects for pages.
    Returns {'username': ..., 'role': ...}."""
    user = get_current_user(request)
    if not user:
        if request.url.path.startswith("/api/"):
            raise HTTPException(status_code=401, detail="Not authenticated")
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return user


def require_operator(request: Request) -> dict:
    """FastAPI dependency — like require_auth but only allows operators."""
    user = require_auth(request)
    if user["role"] != "operator":
        raise HTTPException(status_code=403, detail="Operator access required")
    return user


def clear_session(response: Response) -> None:
    """Remove session cookie."""
    response.delete_cookie(COOKIE_NAME)
