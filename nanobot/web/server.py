"""Web server for nanobot — REST API + WebSocket + static file serving.

This module is the thin orchestrator that assembles routes from domain API
modules and creates the Starlette application. All handler logic lives in
the ``nanobot.web.api.*`` submodules.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

# ---------------------------------------------------------------------------
# Domain API imports
# ---------------------------------------------------------------------------
from nanobot.web.shared import set_globals, manager
from nanobot.web.api.auth import (
    api_auth_login, api_auth_refresh, api_auth_me,
    api_auth_logout, api_auth_change_password,
)
from nanobot.web.api.chat import (
    ws_chat, api_conversations_list, api_conversations_messages,
    api_conversations_delete, api_conversations_rename,
    api_feedback, api_chat_sessions_list, api_chat_sessions_create,
)
from nanobot.web.api.dashboard import (
    api_status, api_sessions, api_session_detail, api_delete_session,
    api_config_get, api_models, api_settings_get, api_quota_check,
    api_stats, api_stats_trends,
)
from nanobot.web.api.employees import (
    api_employees_list, api_employees_detail, api_employees_create,
    api_employees_update, api_employees_delete,
    api_employee_memory_get, api_employee_memory_update, api_employee_memory_delete,
)
from nanobot.web.api.users import (
    api_users_list, api_users_create, api_users_update, api_audit_logs,
)
from nanobot.web.api.files import (
    api_files_list, api_files_upload, api_files_download,
    api_files_delete, api_files_mkdir,
)
from nanobot.web.api.tasks import (
    api_tasks_list, api_tasks_create, api_task_detail,
    api_task_execute, api_task_approve,
)
from nanobot.web.api.knowledge import (
    kb_list, kb_create, kb_get, kb_delete,
    kb_upload_document, kb_delete_document, kb_get_content, kb_search,
)
from nanobot.web.api.keys import api_keys_list, api_keys_create, api_keys_revoke
from nanobot.web.api.external import api_v1_chat, api_v1_employees_list, api_v1_webhook
from nanobot.web.api.health import health_check, health_db

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

def create_app(bus, config, sessions, model: str) -> Starlette:
    """Create the Starlette web application."""
    # Initialize shared globals used by all API modules
    set_globals(bus, config, sessions, model)

    routes = [
        # Health checks (no auth required — handled before middleware)
        Route("/health", health_check, methods=["GET"]),
        Route("/health/db", health_db, methods=["GET"]),
        # Auth routes (public)
        Route("/api/auth/login", api_auth_login, methods=["POST"]),
        Route("/api/auth/refresh", api_auth_refresh, methods=["POST"]),
        Route("/api/auth/logout", api_auth_logout, methods=["POST"]),
        Route("/api/auth/me", api_auth_me, methods=["GET"]),
        Route("/api/auth/change-password", api_auth_change_password, methods=["POST"]),
        # API routes
        Route("/api/status", api_status),
        Route("/api/stats", api_stats, methods=["GET"]),
        Route("/api/stats/trends", api_stats_trends, methods=["GET"]),
        Route("/api/feedback", api_feedback, methods=["POST"]),
        Route("/api/tasks", api_tasks_list, methods=["GET"]),
        Route("/api/tasks", api_tasks_create, methods=["POST"]),
        Route("/api/tasks/{id}", api_task_detail, methods=["GET", "PUT", "DELETE"]),
        Route("/api/tasks/{id}/execute", api_task_execute, methods=["POST"]),
        Route("/api/tasks/{id}/approve", api_task_approve, methods=["POST"]),
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
        # Conversations
        Route("/api/conversations", api_conversations_list, methods=["GET"]),
        Route("/api/conversations/{id}/messages", api_conversations_messages, methods=["GET"]),
        Route("/api/conversations/{id}", api_conversations_delete, methods=["DELETE"]),
        Route("/api/conversations/{id}/rename", api_conversations_rename, methods=["PUT"]),
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
