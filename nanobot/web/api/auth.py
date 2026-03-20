"""Auth API — login, refresh, me, logout, change-password."""

from __future__ import annotations

from datetime import datetime

from starlette.requests import Request
from starlette.responses import JSONResponse


async def api_auth_login(request: Request) -> JSONResponse:
    """Login with username/password, return JWT tokens."""
    from nanobot.auth.jwt_auth import verify_password, create_access_token, create_refresh_token
    from nanobot.db.engine import get_db
    from nanobot.db.models import User, AuditLog
    from sqlalchemy import select

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username or not password:
        return JSONResponse({"error": "username and password required"}, status_code=400)

    async with get_db() as db:
        result = await db.execute(select(User).where(User.username == username))
        user = result.scalar_one_or_none()

        if not user or not verify_password(password, user.password_hash):
            return JSONResponse({"error": "invalid credentials"}, status_code=401)

        if not user.is_active:
            return JSONResponse({"error": "account disabled"}, status_code=403)

        # Update last login
        user.last_login_at = datetime.now()

        # Audit log
        db.add(AuditLog(
            user_id=user.id, username=user.username,
            action="login", resource_type="user",
            ip_address=request.client.host if request.client else None,
        ))

        access_token = create_access_token(user.id, user.username, user.role)
        refresh_token = create_refresh_token(user.id)

    response = JSONResponse({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
            "avatar": user.avatar,
        },
    })
    response.set_cookie(
        "nonobot_token", access_token,
        httponly=True, samesite="lax", max_age=86400,
    )
    return response


async def api_auth_refresh(request: Request) -> JSONResponse:
    """Refresh access token using a refresh token."""
    from nanobot.auth.jwt_auth import decode_token, create_access_token
    from nanobot.db.engine import get_db
    from nanobot.db.models import User
    from sqlalchemy import select

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    refresh_token = body.get("refresh_token", "")
    payload = decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        return JSONResponse({"error": "invalid refresh token"}, status_code=401)

    async with get_db() as db:
        result = await db.execute(select(User).where(User.id == payload["sub"]))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            return JSONResponse({"error": "user not found"}, status_code=401)

    access_token = create_access_token(user.id, user.username, user.role)
    response = JSONResponse({"access_token": access_token})
    response.set_cookie(
        "nonobot_token", access_token,
        httponly=True, samesite="lax", max_age=86400,
    )
    return response


async def api_auth_me(request: Request) -> JSONResponse:
    """Get current user info."""
    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    return JSONResponse({
        "id": user.get("sub"),
        "username": user.get("username"),
        "role": user.get("role"),
    })


async def api_auth_logout(request: Request) -> JSONResponse:
    """Logout — clear the auth cookie."""
    response = JSONResponse({"ok": True})
    response.delete_cookie("nonobot_token")
    return response


async def api_auth_change_password(request: Request) -> JSONResponse:
    """Change the current user's password."""
    from nanobot.auth.jwt_auth import verify_password, hash_password
    from nanobot.db.engine import get_db
    from nanobot.db.models import User, AuditLog
    from sqlalchemy import select

    user = getattr(request.state, "user", {})
    if not user.get("sub"):
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    old_password = body.get("old_password", "")
    new_password = body.get("new_password", "")
    if not old_password or not new_password:
        return JSONResponse({"error": "old_password and new_password required"}, status_code=400)
    if len(new_password) < 4:
        return JSONResponse({"error": "password must be at least 4 characters"}, status_code=400)

    async with get_db() as db:
        result = await db.execute(select(User).where(User.id == user["sub"]))
        db_user = result.scalar_one_or_none()
        if not db_user:
            return JSONResponse({"error": "user not found"}, status_code=404)
        if not verify_password(old_password, db_user.password_hash):
            return JSONResponse({"error": "incorrect current password"}, status_code=400)

        db_user.password_hash = hash_password(new_password)
        db.add(AuditLog(
            user_id=db_user.id, username=db_user.username,
            action="change_password", resource_type="user",
            ip_address=request.client.host if request.client else None,
        ))

    return JSONResponse({"ok": True})
