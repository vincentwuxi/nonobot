"""Shared infrastructure for web API modules — globals, helpers, WebSocket manager."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.websockets import WebSocket

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
# Sandbox / file path helpers
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


def set_globals(bus, config, sessions, model: str):
    """Initialize module-level globals. Called from create_app."""
    global _bus, _config, _sessions, _agent_model
    _bus = bus
    _config = config
    _sessions = sessions
    _agent_model = model
