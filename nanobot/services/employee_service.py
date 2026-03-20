"""Employee service — CRUD + memory management."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.db.engine import get_db
from nanobot.db.models import Employee, AuditLog
from nanobot.repositories.employee_repo import EmployeeRepository
from nanobot.repositories.audit_repo import AuditRepository


class EmployeeService:
    """Business logic for digital employee management."""

    @staticmethod
    async def list_all() -> list[dict]:
        """List all employees as dicts."""
        async with get_db() as db:
            repo = EmployeeRepository(db)
            employees = await repo.list_all(order_by=Employee.created_at.desc())
        return [{
            "id": e.id, "name": e.name, "slug": e.slug, "avatar": e.avatar,
            "description": e.description, "system_prompt": e.system_prompt,
            "model": e.model, "is_active": e.is_active,
            "tools": e.tools, "skills": e.skills, "channels": e.channels,
            "total_tokens": e.total_tokens, "total_messages": e.total_messages,
        } for e in employees]

    @staticmethod
    async def get_detail(emp_id: str) -> dict | None:
        """Get full employee detail by ID."""
        async with get_db() as db:
            repo = EmployeeRepository(db)
            e = await repo.get_by_id(emp_id)
        if not e:
            return None
        return {
            "id": e.id, "name": e.name, "slug": e.slug, "avatar": e.avatar,
            "description": e.description, "system_prompt": e.system_prompt,
            "model": e.model, "provider": e.provider, "is_active": e.is_active,
            "temperature": e.temperature, "max_tokens": e.max_tokens,
            "tools": e.tools, "skills": e.skills, "channels": e.channels,
            "total_tokens": e.total_tokens, "total_messages": e.total_messages,
        }

    @staticmethod
    async def create(data: dict, *, user: dict | None = None) -> dict:
        """Create a new employee. Returns {"id", "name", "slug"}."""
        async with get_db() as db:
            repo = EmployeeRepository(db)
            audit = AuditRepository(db)
            employee = Employee(
                name=data["name"], slug=data["slug"],
                avatar=data.get("avatar", "🤖"),
                description=data.get("description"),
                system_prompt=data.get("system_prompt"),
                model=data.get("model"),
                provider=data.get("provider"),
                temperature=data.get("temperature", 0.1),
                max_tokens=data.get("max_tokens", 8192),
                tools=data.get("tools", []),
                skills=data.get("skills", []),
                channels=data.get("channels", []),
            )
            await repo.create(employee)
            if user:
                await audit.log(
                    user_id=user.get("sub"), username=user.get("username"),
                    action="create_employee", resource_type="employee",
                    detail={"name": data["name"], "slug": data["slug"]},
                )
        return {"id": employee.id, "name": employee.name, "slug": employee.slug}

    @staticmethod
    async def update(emp_id: str, data: dict) -> bool:
        """Update employee fields. Returns True if found."""
        allowed = (
            "name", "avatar", "description", "system_prompt", "model",
            "provider", "temperature", "max_tokens", "tools", "skills",
            "channels", "knowledge_bases", "is_active", "settings",
        )
        async with get_db() as db:
            repo = EmployeeRepository(db)
            emp = await repo.get_by_id(emp_id)
            if not emp:
                return False
            update_data = {k: v for k, v in data.items() if k in allowed}
            await repo.update(emp, update_data)
        return True

    @staticmethod
    async def delete(emp_id: str) -> bool:
        """Delete an employee. Returns True if found."""
        async with get_db() as db:
            repo = EmployeeRepository(db)
            return await repo.delete_by_id(emp_id)

    # ─────── Memory management ───────

    @staticmethod
    def _get_memory_dir(slug: str, config=None) -> Path:
        """Get the memory directory for a specific employee."""
        if config:
            base = Path(config.workspace_path)
        else:
            base = Path.home() / ".nanobot"
        mem_dir = base / "employees" / slug / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        return mem_dir

    @staticmethod
    async def get_memory(emp_id: str, *, config=None) -> dict | None:
        """Get employee memory data."""
        async with get_db() as db:
            repo = EmployeeRepository(db)
            emp = await repo.get_by_id(emp_id)
        if not emp:
            return None

        mem_dir = EmployeeService._get_memory_dir(emp.slug, config)
        memory_file = mem_dir / "MEMORY.md"
        history_file = mem_dir / "HISTORY.md"

        long_term = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
        history = history_file.read_text(encoding="utf-8") if history_file.exists() else ""

        history_entries = [b.strip() for b in history.strip().split("\n\n") if b.strip()] if history.strip() else []

        return {
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
        }

    @staticmethod
    async def update_memory(emp_id: str, data: dict, *, user: dict | None = None, config=None) -> bool:
        """Update employee memory. Returns False if not found."""
        async with get_db() as db:
            repo = EmployeeRepository(db)
            emp = await repo.get_by_id(emp_id)
            if not emp:
                return False
            slug = emp.slug

            if user:
                audit = AuditRepository(db)
                await audit.log(
                    user_id=user.get("sub"), username=user.get("username"),
                    action="update_memory", resource_type="employee",
                    resource_id=emp_id, detail={"employee": slug},
                )

        mem_dir = EmployeeService._get_memory_dir(slug, config)
        if "long_term_memory" in data:
            (mem_dir / "MEMORY.md").write_text(data["long_term_memory"], encoding="utf-8")
        if "history_entry" in data and data["history_entry"].strip():
            with open(mem_dir / "HISTORY.md", "a", encoding="utf-8") as f:
                f.write(data["history_entry"].rstrip() + "\n\n")
        return True

    @staticmethod
    async def clear_memory(emp_id: str, *, target: str = "all", user: dict | None = None, config=None) -> bool:
        """Clear employee memory. Returns False if not found."""
        async with get_db() as db:
            repo = EmployeeRepository(db)
            emp = await repo.get_by_id(emp_id)
            if not emp:
                return False
            slug = emp.slug

            if user:
                audit = AuditRepository(db)
                await audit.log(
                    user_id=user.get("sub"), username=user.get("username"),
                    action="clear_memory", resource_type="employee",
                    resource_id=emp_id, detail={"employee": slug, "target": target},
                )

        mem_dir = EmployeeService._get_memory_dir(slug, config)
        if target in ("memory", "all"):
            f = mem_dir / "MEMORY.md"
            if f.exists():
                f.write_text("", encoding="utf-8")
        if target in ("history", "all"):
            f = mem_dir / "HISTORY.md"
            if f.exists():
                f.write_text("", encoding="utf-8")
        return True
