"""Chat API — WebSocket, conversations, feedback, chat sessions."""

from __future__ import annotations

from datetime import datetime

from loguru import logger
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.websockets import WebSocket, WebSocketDisconnect

import nanobot.web.shared as shared


async def ws_chat(websocket: WebSocket):
    """WebSocket chat endpoint."""
    conn_id = await shared.manager.connect(websocket)
    chat_id = conn_id

    # Send welcome message
    await shared.manager.send_json(conn_id, {
        "type": "system",
        "content": f"Connected to nanobot. Model: {shared._agent_model}",
    })

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")

            if msg_type == "message" and shared._bus:
                from nanobot.bus.events import InboundMessage
                content = data.get("content", "").strip()
                if not content:
                    continue

                # Use session_key from client or default
                session_key = data.get("session", f"web:{chat_id}")
                employee_id = data.get("employee_id")  # optional: which employee to use

                await shared.manager.send_json(conn_id, {
                    "type": "thinking",
                    "content": "nanobot is thinking...",
                })

                # If employee specified, load its config for the agent
                extra_context = {}
                if employee_id:
                    try:
                        from nanobot.db.engine import get_db
                        from nanobot.db.models import Employee
                        from sqlalchemy import select
                        async with get_db() as db:
                            result = await db.execute(select(Employee).where(Employee.id == employee_id))
                            emp = result.scalar_one_or_none()
                            if emp and emp.is_active:
                                extra_context = {
                                    "employee_id": emp.id,
                                    "employee_name": emp.name,
                                    "system_prompt": emp.system_prompt,
                                    "model": emp.model,
                                }
                                # Update employee message counter
                                emp.total_messages = (emp.total_messages or 0) + 1
                    except Exception as e:
                        logger.warning("Failed to load employee {}: {}", employee_id, e)

                msg = InboundMessage(
                    channel="web",
                    sender_id=conn_id,
                    chat_id=chat_id,
                    content=content,
                    session_key_override=session_key,
                    metadata=extra_context,
                )
                await shared._bus.publish_inbound(msg)

            elif msg_type == "ping":
                await shared.manager.send_json(conn_id, {"type": "pong"})

    except WebSocketDisconnect:
        shared.manager.disconnect(conn_id)
    except Exception as e:
        logger.warning("WebSocket error for {}: {}", conn_id, e)
        shared.manager.disconnect(conn_id)


# ---------------------------------------------------------------------------
# Conversation History API
# ---------------------------------------------------------------------------

async def api_conversations_list(request: Request) -> JSONResponse:
    """List all conversations with optional search."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import ChatSession
    from sqlalchemy import select

    q = request.query_params.get("q", "").strip()
    async with get_db() as db:
        stmt = select(ChatSession).order_by(ChatSession.updated_at.desc()).limit(50)
        if q:
            stmt = stmt.where(ChatSession.title.ilike(f"%{q}%"))
        result = await db.execute(stmt)
        sessions = result.scalars().all()

    items = []
    for s in sessions:
        items.append({
            "id": s.id,
            "key": s.key,
            "title": s.title or s.key,
            "message_count": s.message_count or 0,
            "total_tokens": s.total_tokens or 0,
            "employee_id": s.employee_id,
            "channel": s.channel or "web",
            "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })
    return JSONResponse(items)


async def api_conversations_messages(request: Request) -> JSONResponse:
    """Get messages for a specific conversation."""
    conv_id = request.path_params["id"]

    # Try to load from SessionManager first (in-memory cache)
    if shared._sessions:
        from nanobot.db.engine import get_db
        from nanobot.db.models import ChatSession
        from sqlalchemy import select

        async with get_db() as db:
            result = await db.execute(select(ChatSession).where(ChatSession.id == conv_id))
            cs = result.scalar_one_or_none()
            if not cs:
                return JSONResponse({"error": "not found"}, status_code=404)

        session = shared._sessions.get_or_create(cs.key)
        messages = []
        for m in session.messages:
            role = m.get("role", "")
            content = m.get("content", "")
            if role in ("user", "assistant") and content:
                # Handle multimodal content
                if isinstance(content, list):
                    text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
                    content = " ".join(text_parts) or "[multimodal]"
                messages.append({
                    "role": role,
                    "content": content,
                    "timestamp": m.get("timestamp"),
                })
        return JSONResponse(messages)

    return JSONResponse([])


async def api_conversations_delete(request: Request) -> JSONResponse:
    """Delete a conversation."""
    conv_id = request.path_params["id"]
    from nanobot.db.engine import get_db
    from nanobot.db.models import ChatSession
    from sqlalchemy import select

    async with get_db() as db:
        result = await db.execute(select(ChatSession).where(ChatSession.id == conv_id))
        cs = result.scalar_one_or_none()
        if not cs:
            return JSONResponse({"error": "not found"}, status_code=404)
        # Clear from session manager
        if shared._sessions:
            shared._sessions.invalidate(cs.key)
        await db.delete(cs)
        await db.commit()
    return JSONResponse({"status": "deleted"})


async def api_conversations_rename(request: Request) -> JSONResponse:
    """Rename a conversation."""
    conv_id = request.path_params["id"]
    data = await request.json()
    title = data.get("title", "").strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)

    from nanobot.db.engine import get_db
    from nanobot.db.models import ChatSession
    from sqlalchemy import select

    async with get_db() as db:
        result = await db.execute(select(ChatSession).where(ChatSession.id == conv_id))
        cs = result.scalar_one_or_none()
        if not cs:
            return JSONResponse({"error": "not found"}, status_code=404)
        cs.title = title
        await db.commit()
    return JSONResponse({"status": "renamed"})


# ---------------------------------------------------------------------------
# Chat Feedback API
# ---------------------------------------------------------------------------

async def api_feedback(request: Request) -> JSONResponse:
    """Submit feedback for an assistant message."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import ChatFeedback

    data = await request.json()
    rating = data.get("rating", 0)
    if rating not in (1, -1):
        return JSONResponse({"error": "rating must be 1 or -1"}, status_code=400)

    user = getattr(request.state, "user", {})
    fb = ChatFeedback(
        user_id=user.get("user_id"),
        employee_id=data.get("employee_id"),
        message_preview=(data.get("message", ""))[:200],
        rating=rating,
    )
    async with get_db() as db:
        db.add(fb)
        await db.commit()

    return JSONResponse({"status": "ok", "id": fb.id})


# ---------------------------------------------------------------------------
# Chat Session DB Persistence API
# ---------------------------------------------------------------------------

async def api_chat_sessions_list(request: Request) -> JSONResponse:
    """List chat sessions (DB-backed) for the current user."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import ChatSession
    from sqlalchemy import select

    user = getattr(request.state, "user", {})
    user_id = user.get("sub")
    employee_id = request.query_params.get("employee_id")

    async with get_db() as db:
        query = select(ChatSession).order_by(ChatSession.updated_at.desc()).limit(50)
        # Admin sees all, others see only their own
        if user.get("role") not in ("superadmin", "org_admin"):
            query = query.where(ChatSession.user_id == user_id)
        if employee_id:
            query = query.where(ChatSession.employee_id == employee_id)
        result = await db.execute(query)
        sessions = result.scalars().all()

    return JSONResponse([{
        "id": s.id, "key": s.key, "title": s.title or "Untitled",
        "employee_id": s.employee_id, "channel": s.channel,
        "message_count": s.message_count, "total_tokens": s.total_tokens,
        "is_archived": s.is_archived,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
    } for s in sessions])


async def api_chat_sessions_create(request: Request) -> JSONResponse:
    """Create/update a chat session record."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import ChatSession
    from sqlalchemy import select

    user = getattr(request.state, "user", {})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    session_key = body.get("key", "")
    if not session_key:
        return JSONResponse({"error": "key required"}, status_code=400)

    async with get_db() as db:
        result = await db.execute(select(ChatSession).where(ChatSession.key == session_key))
        existing = result.scalar_one_or_none()

        if existing:
            if "title" in body: existing.title = body["title"]
            if "message_count" in body: existing.message_count = body["message_count"]
            if "total_tokens" in body: existing.total_tokens = body["total_tokens"]
            if "is_archived" in body: existing.is_archived = body["is_archived"]
        else:
            session = ChatSession(
                key=session_key,
                user_id=user.get("sub"),
                employee_id=body.get("employee_id"),
                channel=body.get("channel", "web"),
                title=body.get("title", "New Chat"),
            )
            db.add(session)

    return JSONResponse({"ok": True})
