"""JWT token management and password hashing for NonoBot."""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

# ─────────────────────── Config ───────────────────────

_SECRET_KEY: str | None = None
_ALGORITHM = "HS256"
_ACCESS_TOKEN_EXPIRE_HOURS = 24
_REFRESH_TOKEN_EXPIRE_DAYS = 30


def get_secret_key() -> str:
    """Get or generate the JWT secret key."""
    global _SECRET_KEY
    if _SECRET_KEY:
        return _SECRET_KEY

    # Try environment variable first
    _SECRET_KEY = os.environ.get("NONOBOT_SECRET_KEY")
    if _SECRET_KEY:
        return _SECRET_KEY

    # Generate and persist to ~/.nanobot/.secret_key
    key_file = os.path.expanduser("~/.nanobot/.secret_key")
    if os.path.exists(key_file):
        with open(key_file) as f:
            _SECRET_KEY = f.read().strip()
    else:
        _SECRET_KEY = secrets.token_hex(32)
        os.makedirs(os.path.dirname(key_file), exist_ok=True)
        with open(key_file, "w") as f:
            f.write(_SECRET_KEY)
        os.chmod(key_file, 0o600)

    return _SECRET_KEY


# ─────────────────────── Password ───────────────────────

def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


# ─────────────────────── JWT Tokens ───────────────────────

def create_access_token(
    user_id: str,
    username: str,
    role: str,
    extra: dict[str, Any] | None = None,
    expires_hours: int = _ACCESS_TOKEN_EXPIRE_HOURS,
) -> str:
    """Create a JWT access token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=expires_hours),
        "type": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, get_secret_key(), algorithm=_ALGORITHM)


def create_refresh_token(user_id: str) -> str:
    """Create a JWT refresh token."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(days=_REFRESH_TOKEN_EXPIRE_DAYS),
        "type": "refresh",
    }
    return jwt.encode(payload, get_secret_key(), algorithm=_ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT token. Returns None if invalid."""
    try:
        return jwt.decode(token, get_secret_key(), algorithms=[_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ─────────────────────── API Key ───────────────────────

def generate_api_key() -> tuple[str, str]:
    """Generate an API key. Returns (raw_key, key_hash)."""
    raw_key = f"nb-{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


def hash_api_key(raw_key: str) -> str:
    """Hash an API key for storage."""
    return hashlib.sha256(raw_key.encode()).hexdigest()
