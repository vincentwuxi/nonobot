"""Task service — CRUD, execute, approve."""

from __future__ import annotations

from datetime import datetime

from nanobot.db.engine import get_db
from nanobot.db.models import EmployeeTask, Employee
from nanobot.repositories.task_repo import TaskRepository
from nanobot.repositories.employee_repo import EmployeeRepository


def _task_to_dict(t: EmployeeTask) -> dict:
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


class TaskService:
    """Business logic for employee task management."""

    @staticmethod
    async def list_all() -> list[dict]:
        """List all tasks with employee data."""
        async with get_db() as db:
            repo = TaskRepository(db)
            tasks = await repo.list_with_employees()
        return [_task_to_dict(t) for t in tasks]

    @staticmethod
    async def create(data: dict, *, user: dict | None = None) -> dict:
        """Create a new task."""
        title = data.get("title", "").strip()
        if not title:
            raise ValueError("title required")

        employee_id = data.get("employee_id")
        if employee_id:
            employee_id = str(employee_id)
            async with get_db() as db:
                emp_repo = EmployeeRepository(db)
                if not await emp_repo.get_by_id(employee_id):
                    raise ValueError("employee not found")

        async with get_db() as db:
            repo = TaskRepository(db)
            task = EmployeeTask(
                title=title,
                description=data.get("description", ""),
                employee_id=employee_id or None,
                assigned_by=user.get("user_id") if user else None,
                priority=data.get("priority", "medium"),
                schedule=data.get("schedule") or None,
            )
            await repo.create(task)
        return {"id": task.id, "status": "created"}

    @staticmethod
    async def get_detail(task_id: str) -> dict | None:
        """Get task with employee info."""
        async with get_db() as db:
            repo = TaskRepository(db)
            task = await repo.get_with_employee(task_id)
        return _task_to_dict(task) if task else None

    @staticmethod
    async def update(task_id: str, data: dict) -> dict | None:
        """Update a task. Returns updated dict or None."""
        async with get_db() as db:
            repo = TaskRepository(db)
            task = await repo.get_with_employee(task_id)
            if not task:
                return None

            for field in ("title", "description", "employee_id", "priority", "schedule", "status", "result"):
                if field in data:
                    setattr(task, field, data[field] or (None if field in ("employee_id", "schedule") else ""))
            if data.get("status") == "running" and not task.started_at:
                task.started_at = datetime.utcnow()
            if data.get("status") in ("completed", "failed") and not task.completed_at:
                task.completed_at = datetime.utcnow()
            await db.flush()
            await db.refresh(task)
        return _task_to_dict(task)

    @staticmethod
    async def delete(task_id: str) -> bool:
        """Delete a task. Returns True if found."""
        async with get_db() as db:
            repo = TaskRepository(db)
            return await repo.delete_by_id(task_id)

    @staticmethod
    async def approve(task_id: str, *, user: dict | None = None) -> str | None:
        """Approve a pending task. Returns error string or None."""
        async with get_db() as db:
            repo = TaskRepository(db)
            task = await repo.get_by_id(task_id)
            if not task:
                return "not found"
            if task.status != "pending":
                return "task not in pending state"
            task.status = "approved"
            task.result = f"Approved by {user.get('username', 'system') if user else 'system'}"
        return None

    @staticmethod
    async def execute(task_id: str) -> dict | str:
        """Start task execution. Returns dict with status or error string."""
        import asyncio

        async with get_db() as db:
            repo = TaskRepository(db)
            task = await repo.get_with_employee(task_id)
            if not task:
                return "not found"
            if not task.employee_id:
                return "no employee assigned"

            prompt = f"Task: {task.title}"
            if task.description:
                prompt += f"\n\nDetails: {task.description}"
            prompt += "\n\nPlease complete this task and provide a detailed result."

            task.status = "running"
            task.started_at = datetime.utcnow()

        asyncio.create_task(_run_task_agent(task_id, task.employee_id, prompt))
        return {"status": "running", "task_id": task_id}


async def _run_task_agent(task_id: str, employee_id: str, prompt: str):
    """Background task execution."""
    import traceback
    from nanobot.db.engine import get_db
    from nanobot.db.models import EmployeeTask, Employee
    from nanobot.repositories.task_repo import TaskRepository
    from nanobot.repositories.employee_repo import EmployeeRepository

    try:
        async with get_db() as db:
            emp_repo = EmployeeRepository(db)
            emp = await emp_repo.get_by_id(employee_id)
        if not emp:
            raise RuntimeError(f"Employee {employee_id} not found")

        result_text = f"Task queued for employee {emp.name} (agent execution not available in current context)"

        async with get_db() as db:
            repo = TaskRepository(db)
            task = await repo.get_by_id(task_id)
            if task:
                task.status = "completed"
                task.result = result_text
                task.completed_at = datetime.utcnow()
    except Exception as e:
        async with get_db() as db:
            repo = TaskRepository(db)
            task = await repo.get_by_id(task_id)
            if task:
                task.status = "failed"
                task.result = f"Error: {str(e)}\n{traceback.format_exc()}"
                task.completed_at = datetime.utcnow()
