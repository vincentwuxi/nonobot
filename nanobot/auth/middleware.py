"""Starlette authentication middleware for NonoBot web console."""

from __future__ import annotations

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
})

_PUBLIC_PREFIXES = (
    "/static/",
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
    # 1. Authorization: Bearer <token>
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    # 2. API Key: X-API-Key header
    api_key = request.headers.get("x-api-key")
    if api_key:
        return None  # handled separately

    # 3. Cookie
    return request.cookies.get("nonobot_token")


async def auth_middleware(request: Request, call_next):
    """Authentication middleware for Starlette."""
    path = request.url.path

    # Skip auth for public paths
    if _is_public(path):
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

    # Extract and validate token
    token = _extract_token(request)
    if not token:
        # API requests get 401, page requests get redirect
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


def require_role(*roles: str):
    """Decorator-style check for endpoint handlers."""
    def check(request: Request) -> dict[str, Any] | None:
        user = getattr(request.state, "user", None)
        if not user:
            return None
        if user.get("role") not in roles and "superadmin" not in roles:
            return None
        return user
    return check
