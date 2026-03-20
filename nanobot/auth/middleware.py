"""Starlette authentication middleware for NonoBot web console."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from nanobot.auth.jwt_auth import decode_token

# Paths that do NOT require authentication
_PUBLIC_PATHS = frozenset({
    "/api/auth/login",
    "/api/auth/refresh",
    "/login",
    "/api/status",
    "/health",
})

_PUBLIC_PREFIXES = (
    "/static/",
    "/api/v1/",  # External API — authenticated via API key inside handlers
    "/health/",  # Health check sub-endpoints (e.g. /health/db)
)


def _is_public(path: str) -> bool:
    """Check if a path is publicly accessible."""
    if path in _PUBLIC_PATHS:
        return True
    for prefix in _PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _extract_token(request: Request) -> str | None:
    """Extract JWT token from Authorization header or cookie."""
    # 1. Authorization: Bearer <token> (but not API keys starting with nb-)
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer ") and not auth_header[7:].startswith("nb-"):
        return auth_header[7:]

    # 2. Cookie
    return request.cookies.get("nonobot_token")


async def _authenticate_api_key(request: Request) -> dict | None:
    """Authenticate via API key. Returns user-like dict or None."""
    from nanobot.auth.jwt_auth import hash_api_key
    from nanobot.db.engine import get_db
    from nanobot.db.models import ApiKey, User
    from sqlalchemy import select

    # Extract API key from X-API-Key header or Authorization: Bearer nb-xxx
    api_key = request.headers.get("x-api-key")
    if not api_key:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer nb-"):
            api_key = auth_header[7:]

    if not api_key:
        return None

    key_hash = hash_api_key(api_key)

    try:
        async with get_db() as db:
            result = await db.execute(
                select(ApiKey).where(ApiKey.key_hash == key_hash, ApiKey.is_active == True)
            )
            key_record = result.scalar_one_or_none()
            if not key_record:
                return None

            # Check expiry
            if key_record.expires_at and key_record.expires_at < datetime.now():
                return None

            # Update last_used_at
            key_record.last_used_at = datetime.now()

            # Load the associated user
            user_result = await db.execute(
                select(User).where(User.id == key_record.user_id)
            )
            user = user_result.scalar_one_or_none()
            if not user or not user.is_active:
                return None

            return {
                "sub": user.id,
                "username": user.username,
                "role": user.role,
                "api_key_id": key_record.id,
                "api_key_scopes": key_record.scopes or [],
            }
    except Exception:
        return None


async def auth_middleware(request: Request, call_next):
    """Authentication middleware for Starlette."""
    path = request.url.path

    # Skip auth for public paths
    if _is_public(path):
        # For /api/v1/ paths, try API key auth but don't block
        if path.startswith("/api/v1/"):
            user_info = await _authenticate_api_key(request)
            request.state.user = user_info  # may be None — handlers check
        return await call_next(request)

    # WebSocket connections — check query param or cookie
    if path == "/ws":
        token = request.query_params.get("token") or request.cookies.get("nonobot_token")
        if token:
            payload = decode_token(token)
            if payload:
                request.state.user = payload
                return await call_next(request)
        # Allow unauthenticated WS for now (backward compat)
        request.state.user = None
        return await call_next(request)

    # Try API key auth first
    api_key_user = await _authenticate_api_key(request)
    if api_key_user:
        request.state.user = api_key_user
        return await call_next(request)

    # Extract and validate JWT token
    token = _extract_token(request)
    if not token:
        if path.startswith("/api/"):
            return JSONResponse({"error": "authentication required"}, status_code=401)
        return RedirectResponse("/login", status_code=302)

    payload = decode_token(token)
    if not payload:
        if path.startswith("/api/"):
            return JSONResponse({"error": "invalid or expired token"}, status_code=401)
        return RedirectResponse("/login", status_code=302)

    # Attach user info to request state
    request.state.user = payload
    return await call_next(request)
