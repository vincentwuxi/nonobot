"""Knowledge repository — KnowledgeBase + KnowledgeDocument queries."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from nanobot.db.models import KnowledgeBase, KnowledgeDocument
from nanobot.repositories.base import BaseRepository


class KnowledgeBaseRepository(BaseRepository[KnowledgeBase]):
    """Data access for KnowledgeBase entities."""

    model = KnowledgeBase

    async def get_with_documents(self, kb_id: str) -> KnowledgeBase | None:
        """Get KB with its documents preloaded."""
        result = await self.session.execute(
            select(KnowledgeBase)
            .options(selectinload(KnowledgeBase.documents))
            .where(KnowledgeBase.id == kb_id)
        )
        return result.scalar_one_or_none()


class KnowledgeDocumentRepository(BaseRepository[KnowledgeDocument]):
    """Data access for KnowledgeDocument entities."""

    model = KnowledgeDocument

    async def list_by_kb(self, kb_id: str) -> list[KnowledgeDocument]:
        """List all documents in a knowledge base, ordered by creation."""
        result = await self.session.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.kb_id == kb_id)
            .order_by(KnowledgeDocument.created_at)
        )
        return list(result.scalars().all())

    async def get_by_kb_and_id(self, kb_id: str, doc_id: str) -> KnowledgeDocument | None:
        """Get a specific document within a knowledge base."""
        result = await self.session.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == doc_id,
                KnowledgeDocument.kb_id == kb_id,
            )
        )
        return result.scalar_one_or_none()
