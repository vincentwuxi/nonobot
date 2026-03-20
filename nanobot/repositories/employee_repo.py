"""Employee repository — Employee + Memory queries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nanobot.db.models import Employee
from nanobot.repositories.base import BaseRepository


class EmployeeRepository(BaseRepository[Employee]):
    """Data access for Employee entities."""

    model = Employee

    async def get_by_slug(self, slug: str) -> Employee | None:
        """Find employee by slug."""
        result = await self.session.execute(
            select(Employee).where(Employee.slug == slug)
        )
        return result.scalar_one_or_none()

    async def get_active_by_slug(self, slug: str) -> Employee | None:
        """Find active employee by slug."""
        result = await self.session.execute(
            select(Employee).where(Employee.slug == slug, Employee.is_active == True)
        )
        return result.scalar_one_or_none()

    async def list_active(self) -> list[Employee]:
        """List all active employees ordered by name."""
        result = await self.session.execute(
            select(Employee).where(Employee.is_active == True).order_by(Employee.name)
        )
        return list(result.scalars().all())

    async def list_by_messages(self) -> list[Employee]:
        """List all employees ordered by total messages (desc)."""
        result = await self.session.execute(
            select(Employee).order_by(Employee.total_messages.desc())
        )
        return list(result.scalars().all())

    async def increment_messages(self, employee_id: str, count: int = 1) -> None:
        """Increment message count for an employee."""
        emp = await self.get_by_id(employee_id)
        if emp:
            emp.total_messages = (emp.total_messages or 0) + count
