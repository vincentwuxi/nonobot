"""Audit repository — AuditLog queries + dashboard statistics."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, func, case

from nanobot.db.models import (
    AuditLog, User, Employee, KnowledgeBase,
    KnowledgeDocument, ChatFeedback, ChatSession,
)
from nanobot.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditLog]):
    """Data access for AuditLog and cross-entity statistics."""

    model = AuditLog

    async def list_recent(self, limit: int = 50) -> list[AuditLog]:
        """List recent audit logs, newest first."""
        result = await self.session.execute(
            select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def log(
        self,
        *,
        user_id: str | None = None,
        username: str | None = None,
        action: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        detail: dict | None = None,
        ip_address: str | None = None,
    ) -> AuditLog:
        """Create an audit log entry."""
        entry = AuditLog(
            user_id=user_id, username=username, action=action,
            resource_type=resource_type, resource_id=resource_id,
            detail=detail, ip_address=ip_address,
        )
        self.session.add(entry)
        return entry

    # ─────────────── Dashboard statistics ───────────────

    async def get_dashboard_stats(self) -> dict:
        """Aggregate statistics for the dashboard."""
        user_count = await self.session.scalar(select(func.count()).select_from(User)) or 0
        employee_count = await self.session.scalar(select(func.count()).select_from(Employee)) or 0
        active_employees = await self.session.scalar(
            select(func.count()).select_from(Employee).where(Employee.is_active == True)
        ) or 0
        total_messages = await self.session.scalar(
            select(func.coalesce(func.sum(Employee.total_messages), 0)).select_from(Employee)
        ) or 0
        total_tokens = await self.session.scalar(
            select(func.coalesce(func.sum(Employee.total_tokens), 0)).select_from(Employee)
        ) or 0
        audit_count = await self.session.scalar(select(func.count()).select_from(AuditLog)) or 0

        kb_count = await self.session.scalar(select(func.count()).select_from(KnowledgeBase)) or 0
        kb_docs = await self.session.scalar(select(func.count()).select_from(KnowledgeDocument)) or 0

        fb_total = await self.session.scalar(select(func.count()).select_from(ChatFeedback)) or 0
        fb_positive = await self.session.scalar(
            select(func.count()).select_from(ChatFeedback).where(ChatFeedback.rating > 0)
        ) or 0
        satisfaction = round(fb_positive / fb_total * 100) if fb_total > 0 else 0

        return {
            "users": user_count,
            "employees": employee_count,
            "active_employees": active_employees,
            "total_messages": total_messages,
            "total_tokens": total_tokens,
            "audit_entries": audit_count,
            "knowledge_bases": kb_count,
            "knowledge_docs": kb_docs,
            "feedback_total": fb_total,
            "feedback_positive": fb_positive,
            "satisfaction": satisfaction,
        }

    async def get_activity_trends(self, cutoff: datetime) -> list:
        """Get activity trends by day since cutoff."""
        result = await self.session.execute(
            select(
                func.date(AuditLog.timestamp).label("day"),
                func.count().label("count"),
            )
            .where(AuditLog.timestamp.isnot(None))
            .where(AuditLog.timestamp >= cutoff)
            .group_by(func.date(AuditLog.timestamp))
            .order_by(func.date(AuditLog.timestamp))
        )
        return list(result.all())

    async def get_feedback_trends(self, cutoff: datetime) -> list:
        """Get feedback trends by day since cutoff."""
        result = await self.session.execute(
            select(
                func.date(ChatFeedback.created_at).label("day"),
                func.count().label("count"),
                func.sum(case((ChatFeedback.rating > 0, 1), else_=0)).label("positive"),
            )
            .where(ChatFeedback.created_at.isnot(None))
            .where(ChatFeedback.created_at >= cutoff)
            .group_by(func.date(ChatFeedback.created_at))
            .order_by(func.date(ChatFeedback.created_at))
        )
        return list(result.all())
