"""External API Gateway (v1) — chat, employees, webhook."""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse

import nanobot.web.shared as shared


async def api_v1_chat(request: Request) -> JSONResponse:
    """External API: Send a message to a digital employee and get a response.
    
    Requires API key with 'chat' scope.
    Body: {"message": "...", "employee": "slug", "session_key": "optional"}
    """
    from nanobot.db.engine import get_db
    from nanobot.db.models import Employee, AuditLog
    from sqlalchemy import select

    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse({"error": "API key required"}, status_code=401)

    scopes = user.get("api_key_scopes", [])
    if "chat" not in scopes and "admin" not in scopes:
        return JSONResponse({"error": "insufficient scope, 'chat' required"}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    message = body.get("message", "").strip()
    employee_slug = body.get("employee", "assistant")
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)

    # Find employee
    async with get_db() as db:
        result = await db.execute(
            select(Employee).where(Employee.slug == employee_slug, Employee.is_active == True)
        )
        emp = result.scalar_one_or_none()

    if not emp:
        return JSONResponse({"error": f"employee '{employee_slug}' not found"}, status_code=404)

    # Send message through the bus (async — response delivered via outbound queue)
    if not shared._bus:
        return JSONResponse({"error": "agent not available"}, status_code=503)

    from nanobot.bus.events import InboundMessage

    session_key = body.get("session_key", f"api:{user.get('sub', 'anon')}:{employee_slug}")

    msg = InboundMessage(
        channel="api",
        sender_id=user.get("sub", "anon"),
        chat_id=session_key,
        content=message,
        metadata={
            "employee_id": emp.id,
            "system_prompt": emp.system_prompt,
            "model": emp.model,
            "api_request": True,
        },
        session_key_override=session_key,
    )
    await shared._bus.publish_inbound(msg)

    # Log API usage
    async with get_db() as db:
        db.add(AuditLog(
            user_id=user.get("sub"), username=user.get("username"),
            action="api_chat", resource_type="employee", resource_id=emp.id,
            detail={"employee": employee_slug, "message_length": len(message)},
        ))

    return JSONResponse({
        "status": "accepted",
        "message": "Message dispatched to employee for processing.",
        "employee": employee_slug,
        "session_key": session_key,
    }, status_code=202)


async def api_v1_employees_list(request: Request) -> JSONResponse:
    """External API: List available employees."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import Employee
    from sqlalchemy import select

    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse({"error": "API key required"}, status_code=401)

    async with get_db() as db:
        result = await db.execute(
            select(Employee).where(Employee.is_active == True).order_by(Employee.name)
        )
        employees = result.scalars().all()

    return JSONResponse([{
        "slug": e.slug, "name": e.name, "avatar": e.avatar,
        "description": e.description,
    } for e in employees])


async def api_v1_webhook(request: Request) -> JSONResponse:
    """External API: Receive webhook events from third-party systems.
    
    Body: {"event": "...", "employee": "slug", "payload": {...}, "signature": "optional"}
    """
    from nanobot.db.engine import get_db
    from nanobot.db.models import Employee, AuditLog
    from sqlalchemy import select

    user = getattr(request.state, "user", None)
    if not user:
        return JSONResponse({"error": "API key required"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    event_type = body.get("event", "")
    employee_slug = body.get("employee", "assistant")
    payload = body.get("payload", {})

    if not event_type:
        return JSONResponse({"error": "event type required"}, status_code=400)

    # Find employee
    async with get_db() as db:
        result = await db.execute(
            select(Employee).where(Employee.slug == employee_slug, Employee.is_active == True)
        )
        emp = result.scalar_one_or_none()

    if not emp:
        return JSONResponse({"error": f"employee '{employee_slug}' not found"}, status_code=404)

    # Format webhook event as a message to the employee
    webhook_message = f"[Webhook Event: {event_type}]\n{json.dumps(payload, ensure_ascii=False, indent=2)}"

    if shared._bus:
        from nanobot.bus.events import InboundMessage
        session_key = f"webhook:{user.get('sub', 'anon')}:{employee_slug}"
        msg = InboundMessage(
            channel="webhook",
            sender_id=user.get("sub", "anon"),
            chat_id=session_key,
            content=webhook_message,
            metadata={
                "employee_id": emp.id,
                "system_prompt": emp.system_prompt,
                "model": emp.model,
                "webhook_event": event_type,
            },
            session_key_override=session_key,
        )
        await shared._bus.publish_inbound(msg)

    # Log webhook
    async with get_db() as db:
        db.add(AuditLog(
            user_id=user.get("sub"), username=user.get("username"),
            action="webhook_received", resource_type="employee", resource_id=emp.id,
            detail={"event": event_type, "employee": employee_slug},
        ))

    return JSONResponse({
        "status": "accepted",
        "event": event_type,
        "employee": employee_slug,
        "message": f"Webhook event '{event_type}' dispatched to {emp.name}.",
    })
