"""Tasks API — thin controller using TaskService."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from nanobot.services.task_service import TaskService


async def api_tasks_list(request: Request) -> JSONResponse:
    """List all tasks."""
    return JSONResponse(await TaskService.list_all())


async def api_tasks_create(request: Request) -> JSONResponse:
    """Create a new task."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    user = getattr(request.state, "user", {})
    try:
        result = await TaskService.create(data, user=user)
        return JSONResponse(result, status_code=201)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_task_detail(request: Request) -> JSONResponse:
    """Get, update, or delete a task."""
    task_id = request.path_params["id"]

    if request.method == "DELETE":
        ok = await TaskService.delete(task_id)
        if not ok:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse({"status": "deleted"})

    if request.method == "PUT":
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        result = await TaskService.update(task_id, data)
        if not result:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(result)

    # GET
    result = await TaskService.get_detail(task_id)
    if not result:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(result)


async def api_task_approve(request: Request) -> JSONResponse:
    """Approve a pending task."""
    user = getattr(request.state, "user", {})
    err = await TaskService.approve(request.path_params["id"], user=user)
    if err:
        code = 404 if err == "not found" else 400
        return JSONResponse({"error": err}, status_code=code)
    return JSONResponse({"status": "approved", "id": request.path_params["id"]})


async def api_task_execute(request: Request) -> JSONResponse:
    """Execute a task."""
    result = await TaskService.execute(request.path_params["id"])
    if isinstance(result, str):
        code = 404 if result == "not found" else 400
        return JSONResponse({"error": result}, status_code=code)
    return JSONResponse(result)
