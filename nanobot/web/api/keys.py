"""API Keys Management — thin controller using UserService."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from nanobot.services.user_service import UserService


async def api_keys_list(request: Request) -> JSONResponse:
    """List API keys."""
    user = getattr(request.state, "user", {})
    is_admin = user.get("role") in ("superadmin", "org_admin")
    return JSONResponse(await UserService.list_keys(user.get("sub"), is_admin=is_admin))


async def api_keys_create(request: Request) -> JSONResponse:
    """Create a new API key."""
    user = getattr(request.state, "user", {})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)

    scopes = body.get("scopes", ["chat"])
    result = await UserService.create_key(name, scopes, user=user)
    return JSONResponse(result, status_code=201)


async def api_keys_revoke(request: Request) -> JSONResponse:
    """Revoke an API key."""
    user = getattr(request.state, "user", {})
    err = await UserService.revoke_key(request.path_params["id"], user=user)
    if err:
        code = {"not found": 404, "forbidden": 403}.get(err, 400)
        return JSONResponse({"error": err}, status_code=code)
    return JSONResponse({"ok": True})
