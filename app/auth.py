import hashlib
import hmac
import os
import secrets

from fastapi import HTTPException, Request, status

SESSION_COOKIE = "mv_session"


def _admin_user() -> str:
    return os.environ.get("MV_ADMIN_USER", "admin")


def _admin_hash() -> str:
    """Get the SHA-256 hash of the configured admin password. Set MV_ADMIN_PASSWORD env var."""
    pw = os.environ.get("MV_ADMIN_PASSWORD")
    if not pw:
        raise RuntimeError(
            "MV_ADMIN_PASSWORD env var is not set. Set it before starting the server."
        )
    return hashlib.sha256(pw.encode()).hexdigest()


def verify_credentials(username: str, password: str) -> bool:
    try:
        user_ok = hmac.compare_digest(username, _admin_user())
        pw_ok = hmac.compare_digest(
            hashlib.sha256(password.encode()).hexdigest(),
            _admin_hash(),
        )
        return user_ok and pw_ok
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
