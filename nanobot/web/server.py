"""Web server for nanobot — REST API + WebSocket + static file serving."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from nanobot.bus.queue import MessageBus
    from nanobot.config.schema import Config
    from nanobot.session.manager import SessionManager

# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self):
        self.active: dict[str, WebSocket] = {}  # conn_id -> ws
        self._counter = 0

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        self._counter += 1
        conn_id = f"web_{self._counter}"
        self.active[conn_id] = websocket
        logger.info("WebSocket connected: {}", conn_id)
        return conn_id

    def disconnect(self, conn_id: str):
        self.active.pop(conn_id, None)
        logger.info("WebSocket disconnected: {}", conn_id)

    async def send_json(self, conn_id: str, data: dict):
        if ws := self.active.get(conn_id):
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(conn_id)

    async def broadcast(self, data: dict):
        dead = []
        for conn_id, ws in self.active.items():
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(conn_id)
        for cid in dead:
            self.disconnect(cid)


manager = ConnectionManager()

# ---------------------------------------------------------------------------
# Global references (set by create_app)
# ---------------------------------------------------------------------------
_bus: MessageBus | None = None
_config: Config | None = None
_sessions: SessionManager | None = None
_agent_model: str = ""

# ---------------------------------------------------------------------------
# REST API handlers
# ---------------------------------------------------------------------------

def _sandbox_root() -> Path:
    """Return sandbox root directory."""
    if _config:
        return _config.workspace_path
    return Path.home() / ".nanobot" / "sandbox"


def _safe_path(rel: str) -> Path | None:
    """Resolve a relative path inside the sandbox. Returns None if path escapes."""
    root = _sandbox_root().resolve()
    target = (root / rel).resolve()
    try:
        target.relative_to(root)
        return target
    except ValueError:
        return None


async def api_status(request: Request) -> JSONResponse:
    """System status."""
    from nanobot import __version__
    sandbox = _sandbox_root()
    return JSONResponse({
        "status": "running",
        "version": __version__,
        "model": _agent_model,
        "connections": len(manager.active),
        "sessions": len(_sessions.list_sessions()) if _sessions else 0,
        "sandbox": str(sandbox),
        "sandbox_restricted": bool(_config and _config.tools.restrict_to_workspace),
    })


async def api_sessions(request: Request) -> JSONResponse:
    """List all sessions."""
    if not _sessions:
        return JSONResponse([])
    items = _sessions.list_sessions()
    return JSONResponse(items)


async def api_session_detail(request: Request) -> JSONResponse:
    """Get session history."""
    key = request.path_params["key"]
    if not _sessions:
        return JSONResponse({"error": "no session manager"}, status_code=500)
    session = _sessions.get_or_create(key)
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
        "messages": messages[-50:],  # last 50 messages
    })


async def api_config_get(request: Request) -> JSONResponse:
    """Get sanitized config."""
    if not _config:
        return JSONResponse({"error": "no config"}, status_code=500)
    data = _config.model_dump()
    # Mask API keys
    def mask_keys(obj):
        if isinstance(obj, dict):
            return {
                k: ("***" + v[-4:] if isinstance(v, str) and len(v) > 8 and ("key" in k.lower() or "secret" in k.lower() or "token" in k.lower() or "password" in k.lower()) else mask_keys(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [mask_keys(i) for i in obj]
        return obj
    return JSONResponse(mask_keys(data))


async def api_models(request: Request) -> JSONResponse:
    """Get available models from provider."""
    if not _config:
        return JSONResponse([])
    p = _config.get_provider(_config.agents.defaults.model)
    api_base = _config.get_api_base(_config.agents.defaults.model)
    if not p or not p.api_key or not api_base:
        return JSONResponse({"current": _agent_model, "available": []})

    # Try to fetch models from the endpoint
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
                return JSONResponse({"current": _agent_model, "available": models})
    except Exception as e:
        logger.debug("Failed to fetch models: {}", e)

    return JSONResponse({"current": _agent_model, "available": [_agent_model]})


async def api_delete_session(request: Request) -> JSONResponse:
    """Delete a session."""
    key = request.path_params["key"]
    if not _sessions:
        return JSONResponse({"error": "no session manager"}, status_code=500)
    session = _sessions.get_or_create(key)
    session.clear()
    _sessions.save(session)
    _sessions.invalidate(key)
    return JSONResponse({"deleted": key})


# ---------------------------------------------------------------------------
# File management API (sandbox)
# ---------------------------------------------------------------------------

async def api_files_list(request: Request) -> JSONResponse:
    """List files in sandbox directory."""
    rel = request.query_params.get("path", ".")
    target = _safe_path(rel)
    if not target:
        return JSONResponse({"error": "path outside sandbox"}, status_code=403)
    if not target.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    if not target.is_dir():
        return JSONResponse({"error": "not a directory"}, status_code=400)

    items = []
    for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        stat = item.stat()
        items.append({
            "name": item.name,
            "type": "dir" if item.is_dir() else "file",
            "size": stat.st_size if item.is_file() else None,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "path": str(item.relative_to(_sandbox_root().resolve())),
        })
    return JSONResponse({
        "path": rel,
        "items": items,
        "sandbox": str(_sandbox_root()),
    })


async def api_files_upload(request: Request) -> JSONResponse:
    """Upload file to sandbox."""
    form = await request.form()
    upload_file = form.get("file")
    dest_dir = form.get("path", "uploads")

    if not upload_file:
        return JSONResponse({"error": "no file"}, status_code=400)

    target_dir = _safe_path(dest_dir)
    if not target_dir:
        return JSONResponse({"error": "path outside sandbox"}, status_code=403)
    target_dir.mkdir(parents=True, exist_ok=True)

    dest = target_dir / upload_file.filename
    content = await upload_file.read()
    dest.write_bytes(content)

    return JSONResponse({
        "uploaded": upload_file.filename,
        "size": len(content),
        "path": str(dest.relative_to(_sandbox_root().resolve())),
    })


async def api_files_download(request: Request) -> FileResponse:
    """Download a file from sandbox."""
    rel = request.query_params.get("path", "")
    if not rel:
        return JSONResponse({"error": "path required"}, status_code=400)
    target = _safe_path(rel)
    if not target:
        return JSONResponse({"error": "path outside sandbox"}, status_code=403)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)
    return FileResponse(str(target), filename=target.name)


async def api_files_delete(request: Request) -> JSONResponse:
    """Delete a file or empty directory."""
    data = await request.json()
    rel = data.get("path", "")
    if not rel:
        return JSONResponse({"error": "path required"}, status_code=400)
    target = _safe_path(rel)
    if not target:
        return JSONResponse({"error": "path outside sandbox"}, status_code=403)
    if not target.exists():
        return JSONResponse({"error": "not found"}, status_code=404)

    if target.is_file():
        target.unlink()
    elif target.is_dir():
        import shutil
        shutil.rmtree(target)
    return JSONResponse({"deleted": rel})


async def api_files_mkdir(request: Request) -> JSONResponse:
    """Create a directory."""
    data = await request.json()
    rel = data.get("path", "")
    if not rel:
        return JSONResponse({"error": "path required"}, status_code=400)
    target = _safe_path(rel)
    if not target:
        return JSONResponse({"error": "path outside sandbox"}, status_code=403)
    target.mkdir(parents=True, exist_ok=True)
    return JSONResponse({"created": rel})


# ---------------------------------------------------------------------------
# WebSocket handler
# ---------------------------------------------------------------------------

async def ws_chat(websocket: WebSocket):
    """WebSocket chat endpoint."""
    conn_id = await manager.connect(websocket)
    chat_id = conn_id

    # Send welcome message
    await manager.send_json(conn_id, {
        "type": "system",
        "content": f"Connected to nanobot. Model: {_agent_model}",
    })

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")

            if msg_type == "message" and _bus:
                from nanobot.bus.events import InboundMessage
                content = data.get("content", "").strip()
                if not content:
                    continue

                # Use session_key from client or default
                session_key = data.get("session", f"web:{chat_id}")
                employee_id = data.get("employee_id")  # optional: which employee to use

                await manager.send_json(conn_id, {
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
                await _bus.publish_inbound(msg)

            elif msg_type == "ping":
                await manager.send_json(conn_id, {"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(conn_id)
    except Exception as e:
        logger.warning("WebSocket error for {}: {}", conn_id, e)
        manager.disconnect(conn_id)


# ---------------------------------------------------------------------------
# Static fallback for SPA
# ---------------------------------------------------------------------------

STATIC_DIR = Path(__file__).parent / "static"


async def spa_fallback(request: Request) -> HTMLResponse:
    """Serve index.html for all non-API routes (SPA pattern)."""
    index = STATIC_DIR / "index.html"
    if index.exists():
        return HTMLResponse(
            index.read_text(encoding="utf-8"),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return HTMLResponse("<h1>nanobot web — static files not found</h1>", status_code=404)


# ---------------------------------------------------------------------------
# Auth API endpoints
# ---------------------------------------------------------------------------

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
        from datetime import datetime
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


# ---------------------------------------------------------------------------
# Employee (Digital Worker) API
# ---------------------------------------------------------------------------

async def api_employees_list(request: Request) -> JSONResponse:
    """List all digital employees."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import Employee
    from sqlalchemy import select

    async with get_db() as db:
        result = await db.execute(select(Employee).order_by(Employee.created_at.desc()))
        employees = result.scalars().all()

    return JSONResponse([{
        "id": e.id, "name": e.name, "slug": e.slug, "avatar": e.avatar,
        "description": e.description, "model": e.model, "is_active": e.is_active,
        "tools": e.tools, "skills": e.skills, "channels": e.channels,
        "total_tokens": e.total_tokens, "total_messages": e.total_messages,
    } for e in employees])


async def api_employees_create(request: Request) -> JSONResponse:
    """Create a new digital employee."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import Employee, AuditLog

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    name = body.get("name", "").strip()
    slug = body.get("slug", "").strip().lower().replace(" ", "-")
    if not name or not slug:
        return JSONResponse({"error": "name and slug required"}, status_code=400)

    async with get_db() as db:
        employee = Employee(
            name=name, slug=slug,
            avatar=body.get("avatar", "🤖"),
            description=body.get("description"),
            system_prompt=body.get("system_prompt"),
            model=body.get("model"),
            provider=body.get("provider"),
            temperature=body.get("temperature", 0.1),
            max_tokens=body.get("max_tokens", 8192),
            tools=body.get("tools", []),
            skills=body.get("skills", []),
            channels=body.get("channels", []),
        )
        db.add(employee)
        user = getattr(request.state, "user", {})
        db.add(AuditLog(
            user_id=user.get("sub"), username=user.get("username"),
            action="create_employee", resource_type="employee",
            detail={"name": name, "slug": slug},
        ))

    return JSONResponse({"id": employee.id, "name": employee.name, "slug": employee.slug}, status_code=201)


async def api_employees_update(request: Request) -> JSONResponse:
    """Update a digital employee."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import Employee
    from sqlalchemy import select

    emp_id = request.path_params["id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    async with get_db() as db:
        result = await db.execute(select(Employee).where(Employee.id == emp_id))
        employee = result.scalar_one_or_none()
        if not employee:
            return JSONResponse({"error": "not found"}, status_code=404)

        for field in ("name", "avatar", "description", "system_prompt", "model",
                      "provider", "temperature", "max_tokens", "tools", "skills",
                      "channels", "knowledge_bases", "is_active", "settings"):
            if field in body:
                setattr(employee, field, body[field])

    return JSONResponse({"ok": True})


async def api_employees_delete(request: Request) -> JSONResponse:
    """Delete a digital employee."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import Employee
    from sqlalchemy import select

    emp_id = request.path_params["id"]
    async with get_db() as db:
        result = await db.execute(select(Employee).where(Employee.id == emp_id))
        employee = result.scalar_one_or_none()
        if not employee:
            return JSONResponse({"error": "not found"}, status_code=404)
        await db.delete(employee)

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Users API (admin)
# ---------------------------------------------------------------------------

async def api_users_list(request: Request) -> JSONResponse:
    """List all users (admin only)."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import User
    from sqlalchemy import select

    user = getattr(request.state, "user", {})
    if user.get("role") not in ("superadmin", "org_admin"):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async with get_db() as db:
        result = await db.execute(select(User).order_by(User.created_at.desc()))
        users = result.scalars().all()

    return JSONResponse([{
        "id": u.id, "username": u.username, "display_name": u.display_name,
        "email": u.email, "role": u.role, "is_active": u.is_active,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    } for u in users])


async def api_users_create(request: Request) -> JSONResponse:
    """Create a new user (admin only)."""
    from nanobot.auth.jwt_auth import hash_password
    from nanobot.db.engine import get_db
    from nanobot.db.models import User

    user = getattr(request.state, "user", {})
    if user.get("role") not in ("superadmin", "org_admin"):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password:
        return JSONResponse({"error": "username and password required"}, status_code=400)

    async with get_db() as db:
        new_user = User(
            username=username,
            password_hash=hash_password(password),
            display_name=body.get("display_name", username),
            email=body.get("email"),
            role=body.get("role", "member"),
        )
        db.add(new_user)

    return JSONResponse({"id": new_user.id, "username": new_user.username}, status_code=201)


# ---------------------------------------------------------------------------
# Audit API
# ---------------------------------------------------------------------------

async def api_audit_logs(request: Request) -> JSONResponse:
    """List recent audit logs (admin only)."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import AuditLog
    from sqlalchemy import select

    user = getattr(request.state, "user", {})
    if user.get("role") not in ("superadmin", "org_admin"):
        return JSONResponse({"error": "forbidden"}, status_code=403)

    limit = int(request.query_params.get("limit", "50"))
    async with get_db() as db:
        result = await db.execute(
            select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
        )
        logs = result.scalars().all()

    return JSONResponse([{
        "id": l.id, "timestamp": l.timestamp.isoformat() if l.timestamp else None,
        "username": l.username, "action": l.action,
        "resource_type": l.resource_type, "resource_id": l.resource_id,
        "detail": l.detail, "ip_address": l.ip_address,
    } for l in logs])


# ---------------------------------------------------------------------------
# Stats API
# ---------------------------------------------------------------------------

async def api_stats(request: Request) -> JSONResponse:
    """Aggregated statistics for the dashboard."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import User, Employee, AuditLog
    from sqlalchemy import select, func

    async with get_db() as db:
        user_count = await db.scalar(select(func.count()).select_from(User)) or 0
        employee_count = await db.scalar(select(func.count()).select_from(Employee)) or 0
        active_employees = await db.scalar(
            select(func.count()).select_from(Employee).where(Employee.is_active == True)
        ) or 0
        total_messages = await db.scalar(
            select(func.coalesce(func.sum(Employee.total_messages), 0)).select_from(Employee)
        ) or 0
        total_tokens = await db.scalar(
            select(func.coalesce(func.sum(Employee.total_tokens), 0)).select_from(Employee)
        ) or 0
        audit_count = await db.scalar(select(func.count()).select_from(AuditLog)) or 0

        # Recent audit activities (last 10)
        recent = await db.execute(
            select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(10)
        )
        recent_logs = recent.scalars().all()

        # Employee stats breakdown
        emp_result = await db.execute(
            select(Employee).order_by(Employee.total_messages.desc())
        )
        employees = emp_result.scalars().all()

    return JSONResponse({
        "users": user_count,
        "employees": employee_count,
        "active_employees": active_employees,
        "total_messages": total_messages,
        "total_tokens": total_tokens,
        "audit_entries": audit_count,
        "connections": len(manager.active),
        "sessions": len(_sessions.list_sessions()) if _sessions else 0,
        "recent_activity": [{
            "timestamp": l.timestamp.isoformat() if l.timestamp else None,
            "username": l.username, "action": l.action,
        } for l in recent_logs],
        "employee_stats": [{
            "name": e.name, "avatar": e.avatar or '🤖',
            "messages": e.total_messages or 0,
            "tokens": e.total_tokens or 0,
            "is_active": e.is_active,
        } for e in employees],
    })


# ---------------------------------------------------------------------------
# Settings API
# ---------------------------------------------------------------------------

async def api_settings_get(request: Request) -> JSONResponse:
    """Get system settings (admin)."""
    user = getattr(request.state, "user", {})
    return JSONResponse({
        "username": user.get("username"),
        "role": user.get("role"),
        "sandbox_path": str(_sandbox_root()) if _config else None,
        "model": _agent_model,
        "version": getattr(__import__('nanobot'), '__version__', '?'),
    })


# ---------------------------------------------------------------------------
# DB Bootstrap: create default admin
# ---------------------------------------------------------------------------

async def ensure_default_admin():
    """Create the default admin user if no users exist."""
    from nanobot.auth.jwt_auth import hash_password
    from nanobot.db.engine import get_db
    from nanobot.db.models import User, Organization
    from sqlalchemy import select, func

    async with get_db() as db:
        count = await db.scalar(select(func.count()).select_from(User))
        if count and count > 0:
            return

        # Create default org
        org = Organization(name="default", display_name="Default Organization")
        db.add(org)
        await db.flush()

        # Create admin user (password: admin — should be changed!)
        admin = User(
            username="admin",
            password_hash=hash_password("admin"),
            display_name="Administrator",
            role="superadmin",
            org_id=org.id,
        )
        db.add(admin)
        logger.info("Created default admin user (username: admin, password: admin)")

        # Create default digital employee
        from nanobot.db.models import Employee
        employee = Employee(
            name="General Assistant",
            slug="assistant",
            avatar="🤖",
            description="Default AI assistant",
            tools=["read_file", "write_file", "edit_file", "list_dir", "exec",
                   "web_search", "web_fetch", "message", "cron"],
        )
        db.add(employee)
        logger.info("Created default digital employee: General Assistant")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    bus: MessageBus,
    config: Config,
    sessions: SessionManager,
    model: str,
) -> Starlette:
    """Create the Starlette web application."""
    global _bus, _config, _sessions, _agent_model
    _bus = bus
    _config = config
    _sessions = sessions
    _agent_model = model

    routes = [
        # Auth routes (public)
        Route("/api/auth/login", api_auth_login, methods=["POST"]),
        Route("/api/auth/refresh", api_auth_refresh, methods=["POST"]),
        Route("/api/auth/logout", api_auth_logout, methods=["POST"]),
        Route("/api/auth/me", api_auth_me, methods=["GET"]),
        Route("/api/auth/change-password", api_auth_change_password, methods=["POST"]),
        # API routes
        Route("/api/status", api_status),
        Route("/api/stats", api_stats, methods=["GET"]),
        Route("/api/settings", api_settings_get, methods=["GET"]),
        Route("/api/sessions", api_sessions),
        Route("/api/sessions/{key:path}", api_session_detail, methods=["GET"]),
        Route("/api/sessions/{key:path}/delete", api_delete_session, methods=["POST", "DELETE"]),
        Route("/api/config", api_config_get),
        Route("/api/models", api_models),
        # File management API
        Route("/api/files", api_files_list, methods=["GET"]),
        Route("/api/files/upload", api_files_upload, methods=["POST"]),
        Route("/api/files/download", api_files_download, methods=["GET"]),
        Route("/api/files/delete", api_files_delete, methods=["POST"]),
        Route("/api/files/mkdir", api_files_mkdir, methods=["POST"]),
        # Employee (Digital Worker) API
        Route("/api/employees", api_employees_list, methods=["GET"]),
        Route("/api/employees", api_employees_create, methods=["POST"]),
        Route("/api/employees/{id}", api_employees_update, methods=["PUT", "PATCH"]),
        Route("/api/employees/{id}", api_employees_delete, methods=["DELETE"]),
        # Users API (admin)
        Route("/api/users", api_users_list, methods=["GET"]),
        Route("/api/users", api_users_create, methods=["POST"]),
        # Audit API
        Route("/api/audit", api_audit_logs, methods=["GET"]),
        # WebSocket
        WebSocketRoute("/ws", ws_chat),
        # Static files
        Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
        # SPA fallback
        Route("/{path:path}", spa_fallback),
        Route("/", spa_fallback),
    ]

    from starlette.middleware.base import BaseHTTPMiddleware
    from nanobot.auth.middleware import auth_middleware

    middleware = [
        Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]),
        Middleware(BaseHTTPMiddleware, dispatch=auth_middleware),
    ]

    return Starlette(routes=routes, middleware=middleware)

