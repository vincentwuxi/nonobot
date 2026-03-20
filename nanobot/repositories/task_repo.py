"""Task repository — EmployeeTask queries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from nanobot.db.models import EmployeeTask
from nanobot.repositories.base import BaseRepository


class TaskRepository(BaseRepository[EmployeeTask]):
    """Data access for EmployeeTask entities."""

    model = EmployeeTask

    async def get_with_employee(self, task_id: str) -> EmployeeTask | None:
        """Get task with its employee relationship loaded."""
        result = await self.session.execute(
            select(EmployeeTask)
            .options(selectinload(EmployeeTask.employee))
            .where(EmployeeTask.id == task_id)
        )
        return result.scalar_one_or_none()

    async def list_with_employees(self, *, limit: int = 100) -> list[EmployeeTask]:
        """List all tasks with employee data, newest first."""
        result = await self.session.execute(
            select(EmployeeTask)
            .options(selectinload(EmployeeTask.employee))
            .order_by(EmployeeTask.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
