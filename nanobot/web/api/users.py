"""Users + Audit API — thin controller using UserService."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from nanobot.web.shared import require_role
from nanobot.services.user_service import UserService


async def api_users_list(request: Request) -> JSONResponse:
    """List all users (admin only)."""
    if err := require_role(request, "org_admin"):
        return err
    return JSONResponse(await UserService.list_all())


async def api_users_create(request: Request) -> JSONResponse:
    """Create a new user (admin only)."""
    if err := require_role(request, "org_admin"):
        return err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    try:
        result = await UserService.create(body)
        return JSONResponse(result, status_code=201)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


async def api_users_update(request: Request) -> JSONResponse:
    """Update a user (admin only)."""
    if err := require_role(request, "org_admin"):
        return err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    user = getattr(request.state, "user", {})
    ok = await UserService.update(
        request.path_params["id"], body, current_user=user,
    )
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True})


async def api_audit_logs(request: Request) -> JSONResponse:
    """List recent audit logs (admin only)."""
    if err := require_role(request, "org_admin"):
        return err

    limit = int(request.query_params.get("limit", "50"))
    return JSONResponse(await UserService.get_audit_logs(limit))
