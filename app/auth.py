import hashlib
import hmac
import os
import secrets

from fastapi import HTTPException, Request, status

SESSION_COOKIE = "mv_session"


def _admin_hash() -> str:
    """Get the SHA-256 hash of the configured admin password. Set MV_ADMIN_PASSWORD env var."""
    pw = os.environ.get("MV_ADMIN_PASSWORD")
    if not pw:
        raise RuntimeError(
            "MV_ADMIN_PASSWORD env var is not set. Set it before starting the server."
        )
    return hashlib.sha256(pw.encode()).hexdigest()


def verify_password(submitted: str) -> bool:
    try:
        return hmac.compare_digest(
            hashlib.sha256(submitted.encode()).hexdigest(),
            _admin_hash(),
        )
    except RuntimeError:
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def is_authenticated(request: Request, valid_tokens: set[str]) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    return bool(token and token in valid_tokens)


def require_auth(request: Request, valid_tokens: set[str]) -> None:
    if not is_authenticated(request, valid_tokens):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not logged in")
