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

                await manager.send_json(conn_id, {
                    "type": "thinking",
                    "content": "nanobot is thinking...",
                })

                await _bus.publish_inbound(InboundMessage(
                    channel="web",
                    sender_id=conn_id,
                    chat_id=chat_id,
                    content=content,
                    session_key_override=session_key,
                ))

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
        # API routes
        Route("/api/status", api_status),
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
        # WebSocket
        WebSocketRoute("/ws", ws_chat),
        # Static files
        Mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static"),
        # SPA fallback
        Route("/{path:path}", spa_fallback),
        Route("/", spa_fallback),
    ]

    middleware = [
        Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]),
    ]

    return Starlette(routes=routes, middleware=middleware)
