"""Stats service — dashboard statistics + trends."""

from __future__ import annotations

from datetime import datetime, timedelta

from nanobot.db.engine import get_db
from nanobot.db.models import Employee
from nanobot.repositories.audit_repo import AuditRepository
from nanobot.repositories.employee_repo import EmployeeRepository


class StatsService:
    """Business logic for dashboard statistics and trends."""

    @staticmethod
    async def get_dashboard_stats(*, connections: int = 0, sessions_count: int = 0) -> dict:
        """Get aggregated dashboard statistics."""
        async with get_db() as db:
            audit = AuditRepository(db)
            emp_repo = EmployeeRepository(db)

            stats = await audit.get_dashboard_stats()

            # Recent activity
            recent_logs = await audit.list_recent(10)

            # Employee breakdown
            employees = await emp_repo.list_by_messages()

        stats["connections"] = connections
        stats["sessions"] = sessions_count
        stats["recent_activity"] = [{
            "timestamp": l.timestamp.isoformat() if l.timestamp else None,
            "username": l.username, "action": l.action,
        } for l in recent_logs]
        stats["employee_stats"] = [{
            "name": e.name, "avatar": e.avatar or '🤖',
            "messages": e.total_messages or 0,
            "tokens": e.total_tokens or 0,
            "is_active": e.is_active,
        } for e in employees]

        return stats

    @staticmethod
    async def get_trends(days: int = 7) -> dict:
        """Get daily trends for charts."""
        days = min(days, 90)
        cutoff = datetime.utcnow() - timedelta(days=days)

        async with get_db() as db:
            audit = AuditRepository(db)
            activity_rows = await audit.get_activity_trends(cutoff)
            fb_rows = await audit.get_feedback_trends(cutoff)

        # Build date series
        dates = []
        current = (datetime.utcnow() - timedelta(days=days - 1)).date()
        end = datetime.utcnow().date()
        while current <= end:
            dates.append(current.isoformat())
            current += timedelta(days=1)

        # Map data
        activity_map = {str(r[0]): r[1] for r in activity_rows}
        fb_map = {str(r[0]): {"total": r[1], "positive": r[2] or 0} for r in fb_rows}

        return {
            "dates": dates,
            "activity": [activity_map.get(d, 0) for d in dates],
            "feedback": [fb_map.get(d, {}).get("total", 0) for d in dates],
            "satisfaction": [
                round(fb_map[d]["positive"] / fb_map[d]["total"] * 100)
                if d in fb_map and fb_map[d]["total"] > 0 else 0
                for d in dates
            ],
        }
