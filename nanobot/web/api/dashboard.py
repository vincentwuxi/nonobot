"""Dashboard API — thin controller using StatsService + shared globals."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

import nanobot.web.shared as shared
from nanobot.services.stats_service import StatsService
from nanobot.services.user_service import UserService


async def api_status(request: Request) -> JSONResponse:
    """System status."""
    from nanobot import __version__
    sandbox = shared._sandbox_root()
    return JSONResponse({
        "status": "running",
        "version": __version__,
        "model": shared._agent_model,
        "connections": len(shared.manager.active),
        "sessions": len(shared._sessions.list_sessions()) if shared._sessions else 0,
        "sandbox": str(sandbox),
        "sandbox_restricted": bool(shared._config and shared._config.tools.restrict_to_workspace),
    })


async def api_sessions(request: Request) -> JSONResponse:
    """List all sessions."""
    if not shared._sessions:
        return JSONResponse([])
    return JSONResponse(shared._sessions.list_sessions())


async def api_session_detail(request: Request) -> JSONResponse:
    """Get session history."""
    key = request.path_params["key"]
    if not shared._sessions:
        return JSONResponse({"error": "no session manager"}, status_code=500)
    session = shared._sessions.get_or_create(key)
    messages = []
    for m in session.messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if c.get("type") == "text"
            )
        messages.append({
            "role": role,
            "content": content[:2000] if isinstance(content, str) else str(content)[:2000],
            "timestamp": m.get("timestamp", ""),
        })
    return JSONResponse({
        "key": key,
        "message_count": len(session.messages),
        "messages": messages[-50:],
    })


async def api_delete_session(request: Request) -> JSONResponse:
    """Delete a session."""
    key = request.path_params["key"]
    if not shared._sessions:
        return JSONResponse({"error": "no session manager"}, status_code=500)
    session = shared._sessions.get_or_create(key)
    session.clear()
    shared._sessions.save(session)
    shared._sessions.invalidate(key)
    return JSONResponse({"deleted": key})


async def api_config_get(request: Request) -> JSONResponse:
    """Get sanitized config."""
    if not shared._config:
        return JSONResponse({"error": "no config"}, status_code=500)
    data = shared._config.model_dump()

    def mask_keys(obj):
        if isinstance(obj, dict):
            return {
                k: ("***" + v[-4:] if isinstance(v, str) and len(v) > 8 and
                    ("key" in k.lower() or "secret" in k.lower() or
                     "token" in k.lower() or "password" in k.lower())
                    else mask_keys(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [mask_keys(i) for i in obj]
        return obj

    return JSONResponse(mask_keys(data))


async def api_models(request: Request) -> JSONResponse:
    """Get available models from provider."""
    if not shared._config:
        return JSONResponse([])
    p = shared._config.get_provider(shared._config.agents.defaults.model)
    api_base = shared._config.get_api_base(shared._config.agents.defaults.model)
    if not p or not p.api_key or not api_base:
        return JSONResponse({"current": shared._agent_model, "available": []})

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{api_base}/models",
                headers={"Authorization": f"Bearer {p.api_key}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                models = [m["id"] for m in data.get("data", [])]
                return JSONResponse({"current": shared._agent_model, "available": models})
    except Exception as e:
        from loguru import logger
        logger.debug("Failed to fetch models: {}", e)

    return JSONResponse({"current": shared._agent_model, "available": [shared._agent_model]})


async def api_settings_get(request: Request) -> JSONResponse:
    """Get system settings."""
    user = getattr(request.state, "user", {})
    return JSONResponse({
        "username": user.get("username"),
        "role": user.get("role"),
        "sandbox_path": str(shared._sandbox_root()) if shared._config else None,
        "model": shared._agent_model,
        "version": getattr(__import__('nanobot'), '__version__', '?'),
    })


async def api_quota_check(request: Request) -> JSONResponse:
    """Check current user's token quota."""
    user_info = getattr(request.state, "user", {})
    user_id = user_info.get("sub")
    if not user_id:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    result = await UserService.get_quota(user_id)
    if not result:
        return JSONResponse({"error": "user not found"}, status_code=404)
    return JSONResponse(result)


async def api_stats(request: Request) -> JSONResponse:
    """Dashboard statistics — delegates to StatsService."""
    return JSONResponse(await StatsService.get_dashboard_stats(
        connections=len(shared.manager.active),
        sessions_count=len(shared._sessions.list_sessions()) if shared._sessions else 0,
    ))


async def api_stats_trends(request: Request) -> JSONResponse:
    """Daily trends — delegates to StatsService."""
    days = int(request.query_params.get("days", "7"))
    return JSONResponse(await StatsService.get_trends(days))
