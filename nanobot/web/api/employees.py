"""Employees API — thin controller using EmployeeService."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

import nanobot.web.shared as shared
from nanobot.services.employee_service import EmployeeService


async def api_employees_list(request: Request) -> JSONResponse:
    """List all digital employees."""
    return JSONResponse(await EmployeeService.list_all())


async def api_employees_detail(request: Request) -> JSONResponse:
    """Get a single digital employee."""
    result = await EmployeeService.get_detail(request.path_params["id"])
    if not result:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(result)


async def api_employees_create(request: Request) -> JSONResponse:
    """Create a new digital employee."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    name = body.get("name", "").strip()
    slug = body.get("slug", "").strip().lower().replace(" ", "-")
    if not name or not slug:
        return JSONResponse({"error": "name and slug required"}, status_code=400)

    body["name"] = name
    body["slug"] = slug
    user = getattr(request.state, "user", {})
    result = await EmployeeService.create(body, user=user)
    return JSONResponse(result, status_code=201)


async def api_employees_update(request: Request) -> JSONResponse:
    """Update a digital employee."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    ok = await EmployeeService.update(request.path_params["id"], body)
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True})


async def api_employees_delete(request: Request) -> JSONResponse:
    """Delete a digital employee."""
    ok = await EmployeeService.delete(request.path_params["id"])
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True})


# ─────── Memory API ───────

async def api_employee_memory_get(request: Request) -> JSONResponse:
    """Get an employee's memory."""
    result = await EmployeeService.get_memory(
        request.path_params["id"], config=shared._config,
    )
    if not result:
        return JSONResponse({"error": "employee not found"}, status_code=404)
    return JSONResponse(result)


async def api_employee_memory_update(request: Request) -> JSONResponse:
    """Update an employee's memory."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    user = getattr(request.state, "user", {})
    ok = await EmployeeService.update_memory(
        request.path_params["id"], body, user=user, config=shared._config,
    )
    if not ok:
        return JSONResponse({"error": "employee not found"}, status_code=404)
    return JSONResponse({"ok": True})


async def api_employee_memory_delete(request: Request) -> JSONResponse:
    """Clear an employee's memory."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    user = getattr(request.state, "user", {})
    ok = await EmployeeService.clear_memory(
        request.path_params["id"],
        target=body.get("target", "all"),
        user=user,
        config=shared._config,
    )
    if not ok:
        return JSONResponse({"error": "employee not found"}, status_code=404)
    return JSONResponse({"ok": True, "cleared": body.get("target", "all")})
