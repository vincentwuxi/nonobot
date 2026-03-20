"""User repository — User + ApiKey queries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nanobot.db.models import User, ApiKey
from nanobot.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Data access for User entities."""

    model = User

    async def get_by_username(self, username: str) -> User | None:
        """Find user by username."""
        result = await self.session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()


class ApiKeyRepository(BaseRepository[ApiKey]):
    """Data access for ApiKey entities."""

    model = ApiKey

    async def list_for_user(self, user_id: str, *, is_admin: bool = False) -> list[ApiKey]:
        """List API keys — admin sees all, others see only their own."""
        query = select(ApiKey).order_by(ApiKey.created_at.desc())
        if not is_admin:
            query = query.where(ApiKey.user_id == user_id)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_active_by_id(self, key_id: str) -> ApiKey | None:
        """Get an active API key by ID."""
        result = await self.session.execute(
            select(ApiKey).where(ApiKey.id == key_id)
        )
        return result.scalar_one_or_none()
