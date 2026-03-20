"""User service — CRUD, auth, quota, API keys."""

from __future__ import annotations

from typing import Any

from nanobot.db.engine import get_db
from nanobot.db.models import User, ApiKey, AuditLog
from nanobot.repositories.user_repo import UserRepository, ApiKeyRepository
from nanobot.repositories.audit_repo import AuditRepository
from nanobot.web.shared import _mask_ip


class UserService:
    """Business logic for user management."""

    @staticmethod
    async def list_all() -> list[dict]:
        """List all users as dicts."""
        async with get_db() as db:
            repo = UserRepository(db)
            users = await repo.list_all(order_by=User.created_at.desc())
        return [{
            "id": u.id, "username": u.username, "display_name": u.display_name,
            "email": u.email, "role": u.role, "is_active": u.is_active,
            "settings": u.settings or {},
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        } for u in users]

    @staticmethod
    async def create(data: dict) -> dict:
        """Create a new user."""
        from nanobot.auth.jwt_auth import hash_password
        username = data.get("username", "").strip()
        password = data.get("password", "")
        if not username or not password:
            raise ValueError("username and password required")

        user_settings = {
            "daily_token_limit": data.get("daily_token_limit", 100000),
            "monthly_token_limit": data.get("monthly_token_limit", 3000000),
        }

        async with get_db() as db:
            repo = UserRepository(db)
            new_user = User(
                username=username,
                password_hash=hash_password(password),
                display_name=data.get("display_name", username),
                email=data.get("email"),
                role=data.get("role", "member"),
                settings=user_settings,
            )
            await repo.create(new_user)
        return {"id": new_user.id, "username": new_user.username}

    @staticmethod
    async def update(user_id: str, data: dict, *, current_user: dict | None = None) -> bool:
        """Update user. Returns False if not found."""
        async with get_db() as db:
            repo = UserRepository(db)
            audit = AuditRepository(db)
            target = await repo.get_by_id(user_id)
            if not target:
                return False

            update_fields = {}
            for field in ("role", "is_active", "display_name", "email"):
                if field in data:
                    update_fields[field] = data[field]

            # Update quota settings
            settings = target.settings or {}
            for key in ("daily_token_limit", "monthly_token_limit"):
                if key in data:
                    settings[key] = data[key]
            if settings != (target.settings or {}):
                update_fields["settings"] = settings

            await repo.update(target, update_fields)

            if current_user:
                await audit.log(
                    user_id=current_user.get("sub"),
                    username=current_user.get("username"),
                    action="update_user", resource_type="user",
                    resource_id=user_id, detail={"changes": list(data.keys())},
                )
        return True

    @staticmethod
    async def get_quota(user_id: str) -> dict | None:
        """Get user's quota info."""
        async with get_db() as db:
            repo = UserRepository(db)
            u = await repo.get_by_id(user_id)
        if not u:
            return None

        settings = u.settings or {}
        daily_limit = settings.get("daily_token_limit", 100000)
        monthly_limit = settings.get("monthly_token_limit", 3000000)
        daily_used = settings.get("daily_tokens_used", 0)
        monthly_used = settings.get("monthly_tokens_used", 0)

        return {
            "daily_limit": daily_limit, "daily_used": daily_used,
            "daily_remaining": max(0, daily_limit - daily_used),
            "monthly_limit": monthly_limit, "monthly_used": monthly_used,
            "monthly_remaining": max(0, monthly_limit - monthly_used),
            "is_over_quota": daily_used >= daily_limit or monthly_used >= monthly_limit,
        }

    # ─────── Audit logs ───────

    @staticmethod
    async def get_audit_logs(limit: int = 50) -> list[dict]:
        """Get recent audit logs with masked IPs."""
        async with get_db() as db:
            repo = AuditRepository(db)
            logs = await repo.list_recent(limit)

        def _sanitize(detail: dict | None) -> dict | None:
            if not detail:
                return detail
            return {k: v for k, v in detail.items()
                    if k not in ("password_hash", "old_password", "new_password", "secret")}

        return [{
            "id": l.id,
            "timestamp": l.timestamp.isoformat() if l.timestamp else None,
            "username": l.username, "action": l.action,
            "resource_type": l.resource_type, "resource_id": l.resource_id,
            "detail": _sanitize(l.detail),
            "ip_address": _mask_ip(l.ip_address),
        } for l in logs]

    # ─────── API Keys ───────

    @staticmethod
    async def list_keys(user_id: str, *, is_admin: bool = False) -> list[dict]:
        """List API keys."""
        async with get_db() as db:
            repo = ApiKeyRepository(db)
            keys = await repo.list_for_user(user_id, is_admin=is_admin)
        return [{
            "id": k.id, "name": k.name, "key_prefix": k.key_prefix,
            "scopes": k.scopes or [], "is_active": k.is_active,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "created_at": k.created_at.isoformat() if k.created_at else None,
        } for k in keys]

    @staticmethod
    async def create_key(name: str, scopes: list[str], *, user: dict) -> dict:
        """Create a new API key. Returns dict including raw key."""
        from nanobot.auth.jwt_auth import generate_api_key
        raw_key, key_hash = generate_api_key()

        async with get_db() as db:
            repo = ApiKeyRepository(db)
            audit = AuditRepository(db)
            key = ApiKey(
                key_hash=key_hash, key_prefix=raw_key[:10],
                name=name, user_id=user.get("sub"),
                scopes=scopes,
            )
            await repo.create(key)
            await audit.log(
                user_id=user.get("sub"), username=user.get("username"),
                action="create_api_key", resource_type="api_key",
                resource_id=key.id, detail={"name": name, "scopes": scopes},
            )
        return {
            "id": key.id, "name": name, "key": raw_key,
            "key_prefix": raw_key[:10], "scopes": scopes,
            "message": "Save this key now — it won't be shown again!",
        }

    @staticmethod
    async def revoke_key(key_id: str, *, user: dict) -> str | None:
        """Revoke an API key. Returns error string or None on success."""
        async with get_db() as db:
            repo = ApiKeyRepository(db)
            audit = AuditRepository(db)
            key = await repo.get_active_by_id(key_id)
            if not key:
                return "not found"
            if key.user_id != user.get("sub") and user.get("role") not in ("superadmin", "org_admin"):
                return "forbidden"
            key.is_active = False
            await audit.log(
                user_id=user.get("sub"), username=user.get("username"),
                action="revoke_api_key", resource_type="api_key", resource_id=key_id,
            )
        return None
