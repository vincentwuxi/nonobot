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
# RBAC helpers
# ---------------------------------------------------------------------------

_ROLE_HIERARCHY = {
    "superadmin": 5, "org_admin": 4, "team_lead": 3, "member": 2, "guest": 1
}


def require_role(request: Request, min_role: str = "org_admin") -> JSONResponse | None:
    """Check if current user has at least `min_role`. Returns 403 response or None."""
    user = getattr(request.state, "user", {})
    user_level = _ROLE_HIERARCHY.get(user.get("role", ""), 0)
    required_level = _ROLE_HIERARCHY.get(min_role, 99)
    if user_level < required_level:
        return JSONResponse({"error": "forbidden", "required_role": min_role}, status_code=403)
    return None


def _mask_ip(ip: str | None) -> str | None:
    """Mask the last octet of an IP address for privacy."""
    if not ip:
        return None
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.{parts[2]}.***"
    return ip[:len(ip)//2] + "***"  # IPv6 fallback


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
        "description": e.description, "system_prompt": e.system_prompt,
        "model": e.model, "is_active": e.is_active,
        "tools": e.tools, "skills": e.skills, "channels": e.channels,
        "total_tokens": e.total_tokens, "total_messages": e.total_messages,
    } for e in employees])


async def api_employees_detail(request: Request) -> JSONResponse:
    """Get a single digital employee detail."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import Employee
    from sqlalchemy import select

    emp_id = request.path_params["id"]
    async with get_db() as db:
        result = await db.execute(select(Employee).where(Employee.id == emp_id))
        e = result.scalar_one_or_none()
        if not e:
            return JSONResponse({"error": "not found"}, status_code=404)

    return JSONResponse({
        "id": e.id, "name": e.name, "slug": e.slug, "avatar": e.avatar,
        "description": e.description, "system_prompt": e.system_prompt,
        "model": e.model, "provider": e.provider, "is_active": e.is_active,
        "temperature": e.temperature, "max_tokens": e.max_tokens,
        "tools": e.tools, "skills": e.skills, "channels": e.channels,
        "total_tokens": e.total_tokens, "total_messages": e.total_messages,
    })


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

    if err := require_role(request, "org_admin"):
        return err

    async with get_db() as db:
        result = await db.execute(select(User).order_by(User.created_at.desc()))
        users = result.scalars().all()

    return JSONResponse([{
        "id": u.id, "username": u.username, "display_name": u.display_name,
        "email": u.email, "role": u.role, "is_active": u.is_active,
        "settings": u.settings or {},
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    } for u in users])


async def api_users_create(request: Request) -> JSONResponse:
    """Create a new user (admin only)."""
    from nanobot.auth.jwt_auth import hash_password
    from nanobot.db.engine import get_db
    from nanobot.db.models import User

    if err := require_role(request, "org_admin"):
        return err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    username = body.get("username", "").strip()
    password = body.get("password", "")
    if not username or not password:
        return JSONResponse({"error": "username and password required"}, status_code=400)

    # Build settings with quota defaults
    user_settings = {
        "daily_token_limit": body.get("daily_token_limit", 100000),
        "monthly_token_limit": body.get("monthly_token_limit", 3000000),
    }

    async with get_db() as db:
        new_user = User(
            username=username,
            password_hash=hash_password(password),
            display_name=body.get("display_name", username),
            email=body.get("email"),
            role=body.get("role", "member"),
            settings=user_settings,
        )
        db.add(new_user)

    return JSONResponse({"id": new_user.id, "username": new_user.username}, status_code=201)


async def api_users_update(request: Request) -> JSONResponse:
    """Update a user (admin only) — role, settings, quotas."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import User, AuditLog
    from sqlalchemy import select

    if err := require_role(request, "org_admin"):
        return err

    user_id = request.path_params["id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    async with get_db() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        target = result.scalar_one_or_none()
        if not target:
            return JSONResponse({"error": "not found"}, status_code=404)

        if "role" in body:
            target.role = body["role"]
        if "is_active" in body:
            target.is_active = body["is_active"]
        if "display_name" in body:
            target.display_name = body["display_name"]
        if "email" in body:
            target.email = body["email"]

        # Update quota settings
        settings = target.settings or {}
        for key in ("daily_token_limit", "monthly_token_limit"):
            if key in body:
                settings[key] = body[key]
        target.settings = settings

        current_user = getattr(request.state, "user", {})
        db.add(AuditLog(
            user_id=current_user.get("sub"),
            username=current_user.get("username"),
            action="update_user",
            resource_type="user",
            resource_id=user_id,
            detail={"changes": list(body.keys())},
        ))

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Audit API
# ---------------------------------------------------------------------------

async def api_audit_logs(request: Request) -> JSONResponse:
    """List recent audit logs (admin only). IPs are masked for privacy."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import AuditLog
    from sqlalchemy import select

    if err := require_role(request, "org_admin"):
        return err

    limit = int(request.query_params.get("limit", "50"))
    async with get_db() as db:
        result = await db.execute(
            select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
        )
        logs = result.scalars().all()

    # Sanitize: mask IP addresses, filter sensitive detail fields
    def _sanitize_detail(detail: dict | None) -> dict | None:
        if not detail:
            return detail
        return {k: v for k, v in detail.items()
                if k not in ("password_hash", "old_password", "new_password", "secret")}

    return JSONResponse([{
        "id": l.id, "timestamp": l.timestamp.isoformat() if l.timestamp else None,
        "username": l.username, "action": l.action,
        "resource_type": l.resource_type, "resource_id": l.resource_id,
        "detail": _sanitize_detail(l.detail),
        "ip_address": _mask_ip(l.ip_address),
    } for l in logs])


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


async def api_quota_check(request: Request) -> JSONResponse:
    """Check current user's token quota usage."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import User
    from sqlalchemy import select

    user_info = getattr(request.state, "user", {})
    user_id = user_info.get("sub")
    if not user_id:
        return JSONResponse({"error": "not authenticated"}, status_code=401)

    async with get_db() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        u = result.scalar_one_or_none()
        if not u:
            return JSONResponse({"error": "user not found"}, status_code=404)

    settings = u.settings or {}
    daily_limit = settings.get("daily_token_limit", 100000)
    monthly_limit = settings.get("monthly_token_limit", 3000000)
    daily_used = settings.get("daily_tokens_used", 0)
    monthly_used = settings.get("monthly_tokens_used", 0)

    return JSONResponse({
        "daily_limit": daily_limit, "daily_used": daily_used,
        "daily_remaining": max(0, daily_limit - daily_used),
        "monthly_limit": monthly_limit, "monthly_used": monthly_used,
        "monthly_remaining": max(0, monthly_limit - monthly_used),
        "is_over_quota": daily_used >= daily_limit or monthly_used >= monthly_limit,
    })


# ---------------------------------------------------------------------------
# Stats API
# ---------------------------------------------------------------------------

async def api_stats(request: Request) -> JSONResponse:
    """Aggregated statistics for the dashboard."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import User, Employee, AuditLog, KnowledgeBase, KnowledgeDocument, ChatFeedback
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

        # KB stats
        kb_count = await db.scalar(select(func.count()).select_from(KnowledgeBase)) or 0
        kb_docs = await db.scalar(select(func.count()).select_from(KnowledgeDocument)) or 0

        # Feedback stats
        fb_total = await db.scalar(select(func.count()).select_from(ChatFeedback)) or 0
        fb_positive = await db.scalar(
            select(func.count()).select_from(ChatFeedback).where(ChatFeedback.rating > 0)
        ) or 0
        satisfaction = round(fb_positive / fb_total * 100) if fb_total > 0 else 0

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
        "knowledge_bases": kb_count,
        "knowledge_docs": kb_docs,
        "feedback_total": fb_total,
        "feedback_positive": fb_positive,
        "satisfaction": satisfaction,
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
# Task Management API
# ---------------------------------------------------------------------------

def _task_to_dict(t) -> dict:
    return {
        "id": t.id, "title": t.title, "description": t.description,
        "employee_id": t.employee_id,
        "employee_name": t.employee.name if t.employee else None,
        "employee_avatar": (t.employee.avatar or '🤖') if t.employee else None,
        "assigned_by": t.assigned_by,
        "status": t.status, "priority": t.priority,
        "schedule": t.schedule, "result": t.result,
        "token_cost": t.token_cost,
        "started_at": t.started_at.isoformat() if t.started_at else None,
        "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


async def api_tasks_list(request: Request) -> JSONResponse:
    """List all tasks."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import EmployeeTask
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with get_db() as db:
        result = await db.execute(
            select(EmployeeTask).options(selectinload(EmployeeTask.employee))
            .order_by(EmployeeTask.created_at.desc())
        )
        tasks = result.scalars().all()
    return JSONResponse([_task_to_dict(t) for t in tasks])


async def api_tasks_create(request: Request) -> JSONResponse:
    """Create a new task."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import EmployeeTask

    data = await request.json()
    title = data.get("title", "").strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)

    user = getattr(request.state, "user", {})
    task = EmployeeTask(
        title=title,
        description=data.get("description", ""),
        employee_id=data.get("employee_id") or None,
        assigned_by=user.get("user_id"),
        priority=data.get("priority", "medium"),
        schedule=data.get("schedule") or None,
    )
    async with get_db() as db:
        db.add(task)
        await db.commit()
        await db.refresh(task)
    return JSONResponse({"id": task.id, "status": "created"}, status_code=201)


async def api_task_detail(request: Request) -> JSONResponse:
    """Get, update, or delete a task."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import EmployeeTask
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    task_id = request.path_params["id"]

    if request.method == "DELETE":
        async with get_db() as db:
            result = await db.execute(select(EmployeeTask).where(EmployeeTask.id == task_id))
            task = result.scalar_one_or_none()
            if not task:
                return JSONResponse({"error": "not found"}, status_code=404)
            await db.delete(task)
            await db.commit()
        return JSONResponse({"status": "deleted"})

    if request.method == "PUT":
        data = await request.json()
        async with get_db() as db:
            result = await db.execute(
                select(EmployeeTask).options(selectinload(EmployeeTask.employee))
                .where(EmployeeTask.id == task_id)
            )
            task = result.scalar_one_or_none()
            if not task:
                return JSONResponse({"error": "not found"}, status_code=404)
            for field in ("title", "description", "employee_id", "priority", "schedule", "status", "result"):
                if field in data:
                    setattr(task, field, data[field] or (None if field in ("employee_id", "schedule") else ""))
            if data.get("status") == "running" and not task.started_at:
                task.started_at = datetime.utcnow()
            if data.get("status") in ("completed", "failed") and not task.completed_at:
                task.completed_at = datetime.utcnow()
            await db.commit()
            await db.refresh(task)
        return JSONResponse(_task_to_dict(task))

    # GET
    async with get_db() as db:
        result = await db.execute(
            select(EmployeeTask).options(selectinload(EmployeeTask.employee))
            .where(EmployeeTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_task_to_dict(task))


async def api_task_execute(request: Request) -> JSONResponse:
    """Execute a task using the assigned employee's agent."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import EmployeeTask, Employee
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from datetime import datetime

    task_id = request.path_params["id"]
    async with get_db() as db:
        result = await db.execute(
            select(EmployeeTask).options(selectinload(EmployeeTask.employee))
            .where(EmployeeTask.id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not task.employee_id:
            return JSONResponse({"error": "no employee assigned"}, status_code=400)

        # Build prompt from task
        prompt = f"Task: {task.title}"
        if task.description:
            prompt += f"\n\nDetails: {task.description}"
        prompt += "\n\nPlease complete this task and provide a detailed result."

        task.status = "running"
        task.started_at = datetime.utcnow()
        await db.commit()

    # Execute via agent (async, use employee's config)
    import asyncio
    asyncio.create_task(_run_task_agent(task_id, task.employee_id, prompt))

    return JSONResponse({"status": "running", "task_id": task_id})


async def _run_task_agent(task_id: str, employee_id: str, prompt: str):
    """Background task execution via agent."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import EmployeeTask, Employee
    from sqlalchemy import select
    from datetime import datetime
    import traceback

    try:
        # Get agent and employee
        if not _agent:
            raise RuntimeError("Agent not initialized")

        async with get_db() as db:
            emp_result = await db.execute(select(Employee).where(Employee.id == employee_id))
            emp = emp_result.scalar_one_or_none()

        if not emp:
            raise RuntimeError(f"Employee {employee_id} not found")

        # Build context with employee persona
        system_extra = emp.system_prompt or ""
        messages = [{"role": "user", "content": prompt}]

        # Run through agent
        response = await _agent.chat(prompt, system_extra=system_extra, model=emp.model or None)
        result_text = response if isinstance(response, str) else str(response)

        async with get_db() as db:
            t_result = await db.execute(select(EmployeeTask).where(EmployeeTask.id == task_id))
            task = t_result.scalar_one_or_none()
            if task:
                task.status = "completed"
                task.result = result_text
                task.completed_at = datetime.utcnow()
                await db.commit()

    except Exception as e:
        import traceback
        async with get_db() as db:
            t_result = await db.execute(select(EmployeeTask).where(EmployeeTask.id == task_id))
            task = t_result.scalar_one_or_none()
            if task:
                task.status = "failed"
                task.result = f"Error: {str(e)}\n{traceback.format_exc()}"
                task.completed_at = datetime.utcnow()
                await db.commit()


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
# API Key Management
# ---------------------------------------------------------------------------

async def api_keys_list(request: Request) -> JSONResponse:
    """List API keys for the current user (admin sees all)."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import ApiKey
    from sqlalchemy import select

    user = getattr(request.state, "user", {})
    async with get_db() as db:
        query = select(ApiKey).order_by(ApiKey.created_at.desc())
        if user.get("role") not in ("superadmin", "org_admin"):
            query = query.where(ApiKey.user_id == user.get("sub"))
        result = await db.execute(query)
        keys = result.scalars().all()

    return JSONResponse([{
        "id": k.id, "name": k.name, "key_prefix": k.key_prefix,
        "scopes": k.scopes or [], "is_active": k.is_active,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
        "created_at": k.created_at.isoformat() if k.created_at else None,
    } for k in keys])


async def api_keys_create(request: Request) -> JSONResponse:
    """Create a new API key. Returns the raw key ONCE."""
    from nanobot.auth.jwt_auth import generate_api_key
    from nanobot.db.engine import get_db
    from nanobot.db.models import ApiKey, AuditLog

    user = getattr(request.state, "user", {})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)

    raw_key, key_hash = generate_api_key()
    scopes = body.get("scopes", ["chat"])

    async with get_db() as db:
        key = ApiKey(
            key_hash=key_hash,
            key_prefix=raw_key[:10],
            name=name,
            user_id=user.get("sub"),
            scopes=scopes,
        )
        db.add(key)
        db.add(AuditLog(
            user_id=user.get("sub"), username=user.get("username"),
            action="create_api_key", resource_type="api_key", resource_id=key.id,
            detail={"name": name, "scopes": scopes},
        ))

    return JSONResponse({
        "id": key.id, "name": name, "key": raw_key,
        "key_prefix": raw_key[:10], "scopes": scopes,
        "message": "Save this key now — it won't be shown again!",
    }, status_code=201)


async def api_keys_revoke(request: Request) -> JSONResponse:
    """Revoke (deactivate) an API key."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import ApiKey, AuditLog
    from sqlalchemy import select

    user = getattr(request.state, "user", {})
    key_id = request.path_params["id"]

    async with get_db() as db:
        result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
        key = result.scalar_one_or_none()
        if not key:
            return JSONResponse({"error": "not found"}, status_code=404)
        # Only owner or admin can revoke
        if key.user_id != user.get("sub") and user.get("role") not in ("superadmin", "org_admin"):
            return JSONResponse({"error": "forbidden"}, status_code=403)

        key.is_active = False
        db.add(AuditLog(
            user_id=user.get("sub"), username=user.get("username"),
            action="revoke_api_key", resource_type="api_key", resource_id=key_id,
        ))

    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Knowledge Base Management
# ---------------------------------------------------------------------------

async def kb_list(request: Request) -> JSONResponse:
    """List all knowledge bases."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import KnowledgeBase
    from sqlalchemy import select

    async with get_db() as db:
        result = await db.execute(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()))
        kbs = result.scalars().all()

    return JSONResponse([{
        "id": kb.id, "name": kb.name, "description": kb.description,
        "kb_type": kb.kb_type, "is_active": kb.is_active,
        "stats": kb.stats, "created_at": kb.created_at.isoformat() if kb.created_at else None,
    } for kb in kbs])


async def kb_create(request: Request) -> JSONResponse:
    """Create a new knowledge base."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import KnowledgeBase, AuditLog

    user = getattr(request.state, "user", {})

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    name = body.get("name", "").strip()
    if not name:
        return JSONResponse({"error": "name required"}, status_code=400)

    async with get_db() as db:
        kb = KnowledgeBase(
            name=name,
            description=body.get("description", ""),
            kb_type=body.get("kb_type", "file"),
            created_by=user.get("sub"),
            stats={"doc_count": 0, "total_chunks": 0, "total_size": 0},
        )
        db.add(kb)
        db.add(AuditLog(
            user_id=user.get("sub"), username=user.get("username"),
            action="create_kb", resource_type="knowledge_base", resource_id=kb.id,
        ))
        await db.flush()
        kb_id = kb.id

    return JSONResponse({"ok": True, "id": kb_id})


async def kb_get(request: Request) -> JSONResponse:
    """Get a knowledge base with its documents."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import KnowledgeBase, KnowledgeDocument
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    kb_id = request.path_params["id"]

    async with get_db() as db:
        result = await db.execute(
            select(KnowledgeBase).options(selectinload(KnowledgeBase.documents)).where(KnowledgeBase.id == kb_id)
        )
        kb = result.scalar_one_or_none()

    if not kb:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    return JSONResponse({
        "id": kb.id, "name": kb.name, "description": kb.description,
        "kb_type": kb.kb_type, "is_active": kb.is_active, "stats": kb.stats,
        "documents": [{
            "id": doc.id, "filename": doc.filename, "file_size": doc.file_size,
            "chunk_count": doc.chunk_count, "status": doc.status,
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
        } for doc in (kb.documents or [])],
    })


async def kb_delete(request: Request) -> JSONResponse:
    """Delete a knowledge base and its documents."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import KnowledgeBase, AuditLog
    from sqlalchemy import select

    user = getattr(request.state, "user", {})
    kb_id = request.path_params["id"]

    async with get_db() as db:
        result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        kb = result.scalar_one_or_none()
        if not kb:
            return JSONResponse({"error": "not found"}, status_code=404)
        await db.delete(kb)
        db.add(AuditLog(
            user_id=user.get("sub"), username=user.get("username"),
            action="delete_kb", resource_type="knowledge_base", resource_id=kb_id,
        ))

    return JSONResponse({"ok": True})


async def kb_upload_document(request: Request) -> JSONResponse:
    """Upload a document to a knowledge base."""
    import hashlib
    from nanobot.db.engine import get_db
    from nanobot.db.models import KnowledgeBase, KnowledgeDocument, AuditLog
    from sqlalchemy import select

    user = getattr(request.state, "user", {})
    kb_id = request.path_params["id"]

    async with get_db() as db:
        result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        kb = result.scalar_one_or_none()

    if not kb:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)

    # Handle multipart form upload
    form = await request.form()
    uploaded = form.get("file")

    if uploaded:
        content_bytes = await uploaded.read()
        filename = getattr(uploaded, "filename", "unnamed.txt") or "unnamed.txt"
        content = content_bytes.decode("utf-8", errors="replace")
    else:
        # Fallback: JSON body with direct text
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "file or JSON body required"}, status_code=400)
        content = body.get("content", "")
        filename = body.get("filename", "untitled.md")

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    # Simple chunking: split by headings or double newlines
    chunks = [c.strip() for c in content.split("\n\n") if c.strip()]
    chunk_count = len(chunks)

    async with get_db() as db:
        doc = KnowledgeDocument(
            kb_id=kb_id,
            filename=filename,
            content=content,
            content_hash=content_hash,
            file_size=len(content.encode("utf-8")),
            chunk_count=chunk_count,
            status="ready",
        )
        db.add(doc)

        # Update KB stats
        result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        kb = result.scalar_one()
        stats = dict(kb.stats or {})
        stats["doc_count"] = stats.get("doc_count", 0) + 1
        stats["total_chunks"] = stats.get("total_chunks", 0) + chunk_count
        stats["total_size"] = stats.get("total_size", 0) + len(content.encode("utf-8"))
        kb.stats = stats

        db.add(AuditLog(
            user_id=user.get("sub"), username=user.get("username"),
            action="upload_document", resource_type="knowledge_base", resource_id=kb_id,
            detail={"filename": filename, "size": len(content.encode("utf-8"))},
        ))

        await db.flush()
        doc_id = doc.id

    return JSONResponse({"ok": True, "document_id": doc_id, "filename": filename, "chunks": chunk_count})


async def kb_delete_document(request: Request) -> JSONResponse:
    """Delete a document from a knowledge base."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import KnowledgeBase, KnowledgeDocument, AuditLog
    from sqlalchemy import select

    user = getattr(request.state, "user", {})
    kb_id = request.path_params["id"]
    doc_id = request.path_params["doc_id"]

    async with get_db() as db:
        result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == doc_id, KnowledgeDocument.kb_id == kb_id)
        )
        doc = result.scalar_one_or_none()
        if not doc:
            return JSONResponse({"error": "document not found"}, status_code=404)

        # Update KB stats
        result2 = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        kb = result2.scalar_one_or_none()
        if kb:
            stats = dict(kb.stats or {})
            stats["doc_count"] = max(0, stats.get("doc_count", 0) - 1)
            stats["total_chunks"] = max(0, stats.get("total_chunks", 0) - doc.chunk_count)
            stats["total_size"] = max(0, stats.get("total_size", 0) - doc.file_size)
            kb.stats = stats

        await db.delete(doc)
        db.add(AuditLog(
            user_id=user.get("sub"), username=user.get("username"),
            action="delete_document", resource_type="knowledge_base", resource_id=kb_id,
            detail={"doc_id": doc_id, "filename": doc.filename},
        ))

    return JSONResponse({"ok": True})


async def kb_get_content(request: Request) -> JSONResponse:
    """Get the concatenated content of all documents in a KB (for L1 injection)."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import KnowledgeBase, KnowledgeDocument
    from sqlalchemy import select

    kb_id = request.path_params["id"]

    async with get_db() as db:
        result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id).order_by(KnowledgeDocument.created_at)
        )
        docs = result.scalars().all()

    if not docs:
        return JSONResponse({"content": "", "doc_count": 0})

    parts = []
    for doc in docs:
        parts.append(f"## {doc.filename}\n\n{doc.content or ''}")

    return JSONResponse({
        "content": "\n\n---\n\n".join(parts),
        "doc_count": len(docs),
    })


async def kb_search(request: Request) -> JSONResponse:
    """Search across documents in a knowledge base."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import KnowledgeDocument
    from sqlalchemy import select

    kb_id = request.path_params["id"]
    query = request.query_params.get("q", "").strip().lower()

    if not query:
        return JSONResponse({"results": [], "query": ""})

    async with get_db() as db:
        result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id).order_by(KnowledgeDocument.created_at)
        )
        docs = result.scalars().all()

    results = []
    for doc in docs:
        if not doc.content:
            continue
        lines = doc.content.split("\n")
        for i, line in enumerate(lines):
            if query in line.lower():
                # Include context: 1 line before and after
                start = max(0, i - 1)
                end = min(len(lines), i + 2)
                snippet = "\n".join(lines[start:end])
                results.append({
                    "filename": doc.filename,
                    "line_number": i + 1,
                    "snippet": snippet[:300],
                })
                if len(results) >= 20:
                    break
        if len(results) >= 20:
            break

    return JSONResponse({"results": results, "query": query, "count": len(results)})


# ---------------------------------------------------------------------------
# Employee Memory Management
# ---------------------------------------------------------------------------

def _get_employee_memory_dir(slug: str) -> Path:
    """Get the memory directory for a specific employee."""
    base = Path(_config.workspace_path) if _config else Path.home() / ".nanobot"
    mem_dir = base / "employees" / slug / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    return mem_dir


async def api_employee_memory_get(request: Request) -> JSONResponse:
    """Get an employee's memory (long-term + history + stats)."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import Employee
    from sqlalchemy import select

    emp_id = request.path_params["id"]

    async with get_db() as db:
        result = await db.execute(select(Employee).where(Employee.id == emp_id))
        emp = result.scalar_one_or_none()

    if not emp:
        return JSONResponse({"error": "employee not found"}, status_code=404)

    mem_dir = _get_employee_memory_dir(emp.slug)
    memory_file = mem_dir / "MEMORY.md"
    history_file = mem_dir / "HISTORY.md"

    long_term = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
    history = history_file.read_text(encoding="utf-8") if history_file.exists() else ""

    # Parse history entries
    history_entries = []
    if history.strip():
        for block in history.strip().split("\n\n"):
            block = block.strip()
            if block:
                history_entries.append(block)

    return JSONResponse({
        "employee_id": emp.id,
        "employee_name": emp.name,
        "employee_slug": emp.slug,
        "long_term_memory": long_term,
        "history_entries": history_entries,
        "stats": {
            "memory_size_bytes": len(long_term.encode("utf-8")),
            "history_entries_count": len(history_entries),
            "history_size_bytes": len(history.encode("utf-8")),
            "memory_file_exists": memory_file.exists(),
            "history_file_exists": history_file.exists(),
        },
    })


async def api_employee_memory_update(request: Request) -> JSONResponse:
    """Update an employee's long-term memory."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import Employee, AuditLog
    from sqlalchemy import select

    user = getattr(request.state, "user", {})
    emp_id = request.path_params["id"]

    async with get_db() as db:
        result = await db.execute(select(Employee).where(Employee.id == emp_id))
        emp = result.scalar_one_or_none()

    if not emp:
        return JSONResponse({"error": "employee not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    mem_dir = _get_employee_memory_dir(emp.slug)

    # Update long-term memory
    if "long_term_memory" in body:
        memory_file = mem_dir / "MEMORY.md"
        memory_file.write_text(body["long_term_memory"], encoding="utf-8")

    # Optionally append to history
    if "history_entry" in body and body["history_entry"].strip():
        history_file = mem_dir / "HISTORY.md"
        with open(history_file, "a", encoding="utf-8") as f:
            f.write(body["history_entry"].rstrip() + "\n\n")

    async with get_db() as db:
        db.add(AuditLog(
            user_id=user.get("sub"), username=user.get("username"),
            action="update_memory", resource_type="employee", resource_id=emp_id,
            detail={"employee": emp.slug},
        ))

    return JSONResponse({"ok": True})


async def api_employee_memory_delete(request: Request) -> JSONResponse:
    """Clear an employee's memory (long-term and/or history)."""
    from nanobot.db.engine import get_db
    from nanobot.db.models import Employee, AuditLog
    from sqlalchemy import select

    user = getattr(request.state, "user", {})
    emp_id = request.path_params["id"]

    async with get_db() as db:
        result = await db.execute(select(Employee).where(Employee.id == emp_id))
        emp = result.scalar_one_or_none()

    if not emp:
        return JSONResponse({"error": "employee not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        body = {}

    mem_dir = _get_employee_memory_dir(emp.slug)
    target = body.get("target", "all")  # "memory", "history", "all"

    if target in ("memory", "all"):
        memory_file = mem_dir / "MEMORY.md"
        if memory_file.exists():
            memory_file.write_text("", encoding="utf-8")

    if target in ("history", "all"):
        history_file = mem_dir / "HISTORY.md"
        if history_file.exists():
            history_file.write_text("", encoding="utf-8")

    async with get_db() as db:
        db.add(AuditLog(
            user_id=user.get("sub"), username=user.get("username"),
            action="clear_memory", resource_type="employee", resource_id=emp_id,
            detail={"employee": emp.slug, "target": target},
        ))

    return JSONResponse({"ok": True, "cleared": target})


# ---------------------------------------------------------------------------
# External API Gateway (v1)
# ---------------------------------------------------------------------------

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
    if not _bus:
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
    await _bus.publish_inbound(msg)

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

    if _bus:
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
        await _bus.publish_inbound(msg)

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
        Route("/api/feedback", api_feedback, methods=["POST"]),
        Route("/api/tasks", api_tasks_list, methods=["GET"]),
        Route("/api/tasks", api_tasks_create, methods=["POST"]),
        Route("/api/tasks/{id}", api_task_detail, methods=["GET", "PUT", "DELETE"]),
        Route("/api/tasks/{id}/execute", api_task_execute, methods=["POST"]),
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
        Route("/api/employees/{id}", api_employees_detail, methods=["GET"]),
        Route("/api/employees/{id}", api_employees_update, methods=["PUT", "PATCH"]),
        Route("/api/employees/{id}", api_employees_delete, methods=["DELETE"]),
        # Users API (admin)
        Route("/api/users", api_users_list, methods=["GET"]),
        Route("/api/users", api_users_create, methods=["POST"]),
        Route("/api/users/{id}", api_users_update, methods=["PUT", "PATCH"]),
        # Audit API
        Route("/api/audit", api_audit_logs, methods=["GET"]),
        # Chat Sessions (DB)
        Route("/api/chat-sessions", api_chat_sessions_list, methods=["GET"]),
        Route("/api/chat-sessions", api_chat_sessions_create, methods=["POST"]),
        # Quota
        Route("/api/quota", api_quota_check, methods=["GET"]),
        # API Keys
        Route("/api/keys", api_keys_list, methods=["GET"]),
        Route("/api/keys", api_keys_create, methods=["POST"]),
        Route("/api/keys/{id}/revoke", api_keys_revoke, methods=["POST"]),
        # Employee Memory
        Route("/api/employees/{id}/memory", api_employee_memory_get, methods=["GET"]),
        Route("/api/employees/{id}/memory", api_employee_memory_update, methods=["PUT"]),
        Route("/api/employees/{id}/memory", api_employee_memory_delete, methods=["DELETE"]),
        # Knowledge Bases
        Route("/api/knowledge-bases", kb_list, methods=["GET"]),
        Route("/api/knowledge-bases", kb_create, methods=["POST"]),
        Route("/api/knowledge-bases/{id}", kb_get, methods=["GET"]),
        Route("/api/knowledge-bases/{id}", kb_delete, methods=["DELETE"]),
        Route("/api/knowledge-bases/{id}/documents", kb_upload_document, methods=["POST"]),
        Route("/api/knowledge-bases/{id}/documents/{doc_id}", kb_delete_document, methods=["DELETE"]),
        Route("/api/knowledge-bases/{id}/content", kb_get_content, methods=["GET"]),
        Route("/api/knowledge-bases/{id}/search", kb_search, methods=["GET"]),
        # External API (v1) — authenticated via API key
        Route("/api/v1/chat", api_v1_chat, methods=["POST"]),
        Route("/api/v1/employees", api_v1_employees_list, methods=["GET"]),
        Route("/api/v1/webhook", api_v1_webhook, methods=["POST"]),
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

