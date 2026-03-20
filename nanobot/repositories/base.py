"""Generic CRUD repository base class."""

from __future__ import annotations

from typing import Any, Generic, Sequence, Type, TypeVar

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from nanobot.db.models import Base

T = TypeVar("T", bound=Base)


class BaseRepository(Generic[T]):
    """Generic async CRUD repository.

    Usage::

        class UserRepo(BaseRepository[User]):
            model = User

        async with get_db() as db:
            repo = UserRepo(db)
            user = await repo.get_by_id("abc")
            users = await repo.list_all(limit=20)
    """

    model: Type[T]

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, id_: str, *, options: list | None = None) -> T | None:
        """Get a single entity by primary key."""
        stmt = select(self.model).where(self.model.id == id_)
        if options:
            for opt in options:
                stmt = stmt.options(opt)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(
        self,
        *,
        order_by=None,
        filters: list | None = None,
        options: list | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[T]:
        """List entities with optional filtering and ordering."""
        stmt = select(self.model)
        if filters:
            for f in filters:
                stmt = stmt.where(f)
        if options:
            for opt in options:
                stmt = stmt.options(opt)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count(self, *, filters: list | None = None) -> int:
        """Count entities matching optional filters."""
        stmt = select(func.count()).select_from(self.model)
        if filters:
            for f in filters:
                stmt = stmt.where(f)
        return (await self.session.scalar(stmt)) or 0

    async def create(self, entity: T) -> T:
        """Add a new entity to the session."""
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def update(self, entity: T, data: dict[str, Any]) -> T:
        """Update entity fields from a dict."""
        for key, value in data.items():
            if hasattr(entity, key):
                setattr(entity, key, value)
        await self.session.flush()
        return entity

    async def delete(self, entity: T) -> None:
        """Delete an entity."""
        await self.session.delete(entity)
        await self.session.flush()

    async def delete_by_id(self, id_: str) -> bool:
        """Delete by primary key. Returns True if found and deleted."""
        entity = await self.get_by_id(id_)
        if entity:
            await self.delete(entity)
            return True
        return False
