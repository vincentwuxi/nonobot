"""Knowledge service — KB CRUD, document management, search."""

from __future__ import annotations

import hashlib

from nanobot.db.engine import get_db
from nanobot.db.models import KnowledgeBase, KnowledgeDocument, AuditLog
from nanobot.repositories.knowledge_repo import KnowledgeBaseRepository, KnowledgeDocumentRepository
from nanobot.repositories.audit_repo import AuditRepository


class KnowledgeService:
    """Business logic for knowledge base management."""

    @staticmethod
    async def list_all() -> list[dict]:
        """List all knowledge bases."""
        async with get_db() as db:
            repo = KnowledgeBaseRepository(db)
            kbs = await repo.list_all(order_by=KnowledgeBase.created_at.desc())
        return [{
            "id": kb.id, "name": kb.name, "description": kb.description,
            "kb_type": kb.kb_type, "is_active": kb.is_active,
            "stats": kb.stats,
            "created_at": kb.created_at.isoformat() if kb.created_at else None,
        } for kb in kbs]

    @staticmethod
    async def create(data: dict, *, user: dict | None = None) -> str:
        """Create a KB. Returns KB ID."""
        name = data.get("name", "").strip()
        if not name:
            raise ValueError("name required")

        async with get_db() as db:
            repo = KnowledgeBaseRepository(db)
            audit = AuditRepository(db)
            kb = KnowledgeBase(
                name=name,
                description=data.get("description", ""),
                kb_type=data.get("kb_type", "file"),
                created_by=user.get("sub") if user else None,
                stats={"doc_count": 0, "total_chunks": 0, "total_size": 0},
            )
            await repo.create(kb)
            if user:
                await audit.log(
                    user_id=user.get("sub"), username=user.get("username"),
                    action="create_kb", resource_type="knowledge_base", resource_id=kb.id,
                )
            kb_id = kb.id
        return kb_id

    @staticmethod
    async def get_with_documents(kb_id: str) -> dict | None:
        """Get KB with documents."""
        async with get_db() as db:
            repo = KnowledgeBaseRepository(db)
            kb = await repo.get_with_documents(kb_id)
        if not kb:
            return None
        return {
            "id": kb.id, "name": kb.name, "description": kb.description,
            "kb_type": kb.kb_type, "is_active": kb.is_active, "stats": kb.stats,
            "documents": [{
                "id": doc.id, "filename": doc.filename, "file_size": doc.file_size,
                "chunk_count": doc.chunk_count, "status": doc.status,
                "created_at": doc.created_at.isoformat() if doc.created_at else None,
            } for doc in (kb.documents or [])],
        }

    @staticmethod
    async def delete(kb_id: str, *, user: dict | None = None) -> bool:
        """Delete a KB and its documents."""
        async with get_db() as db:
            repo = KnowledgeBaseRepository(db)
            audit = AuditRepository(db)
            kb = await repo.get_by_id(kb_id)
            if not kb:
                return False
            await repo.delete(kb)
            if user:
                await audit.log(
                    user_id=user.get("sub"), username=user.get("username"),
                    action="delete_kb", resource_type="knowledge_base", resource_id=kb_id,
                )
        return True

    @staticmethod
    async def upload_document(
        kb_id: str,
        filename: str,
        content: str,
        *,
        user: dict | None = None,
    ) -> dict | str:
        """Upload a document to a KB. Returns {"document_id", ...} or error string."""
        async with get_db() as db:
            kb_repo = KnowledgeBaseRepository(db)
            doc_repo = KnowledgeDocumentRepository(db)
            audit = AuditRepository(db)

            kb = await kb_repo.get_by_id(kb_id)
            if not kb:
                return "knowledge base not found"

            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            chunks = [c.strip() for c in content.split("\n\n") if c.strip()]
            chunk_count = len(chunks)
            file_size = len(content.encode("utf-8"))

            doc = KnowledgeDocument(
                kb_id=kb_id, filename=filename, content=content,
                content_hash=content_hash, file_size=file_size,
                chunk_count=chunk_count, status="ready",
            )
            await doc_repo.create(doc)

            # Update KB stats
            stats = dict(kb.stats or {})
            stats["doc_count"] = stats.get("doc_count", 0) + 1
            stats["total_chunks"] = stats.get("total_chunks", 0) + chunk_count
            stats["total_size"] = stats.get("total_size", 0) + file_size
            kb.stats = stats

            if user:
                await audit.log(
                    user_id=user.get("sub"), username=user.get("username"),
                    action="upload_document", resource_type="knowledge_base",
                    resource_id=kb_id, detail={"filename": filename, "size": file_size},
                )
            doc_id = doc.id
        return {"document_id": doc_id, "filename": filename, "chunks": chunk_count}

    @staticmethod
    async def delete_document(kb_id: str, doc_id: str, *, user: dict | None = None) -> bool:
        """Delete a document from a KB. Returns False if not found."""
        async with get_db() as db:
            doc_repo = KnowledgeDocumentRepository(db)
            kb_repo = KnowledgeBaseRepository(db)
            audit = AuditRepository(db)

            doc = await doc_repo.get_by_kb_and_id(kb_id, doc_id)
            if not doc:
                return False

            # Update KB stats
            kb = await kb_repo.get_by_id(kb_id)
            if kb:
                stats = dict(kb.stats or {})
                stats["doc_count"] = max(0, stats.get("doc_count", 0) - 1)
                stats["total_chunks"] = max(0, stats.get("total_chunks", 0) - doc.chunk_count)
                stats["total_size"] = max(0, stats.get("total_size", 0) - doc.file_size)
                kb.stats = stats

            await doc_repo.delete(doc)
            if user:
                await audit.log(
                    user_id=user.get("sub"), username=user.get("username"),
                    action="delete_document", resource_type="knowledge_base",
                    resource_id=kb_id, detail={"doc_id": doc_id, "filename": doc.filename},
                )
        return True

    @staticmethod
    async def get_content(kb_id: str) -> dict:
        """Get concatenated content of all documents in a KB."""
        async with get_db() as db:
            doc_repo = KnowledgeDocumentRepository(db)
            docs = await doc_repo.list_by_kb(kb_id)

        if not docs:
            return {"content": "", "doc_count": 0}

        parts = [f"## {doc.filename}\n\n{doc.content or ''}" for doc in docs]
        return {"content": "\n\n---\n\n".join(parts), "doc_count": len(docs)}

    @staticmethod
    async def search(kb_id: str, query: str) -> dict:
        """Search documents in a KB."""
        query_lower = query.strip().lower()
        if not query_lower:
            return {"results": [], "query": "", "count": 0}

        async with get_db() as db:
            doc_repo = KnowledgeDocumentRepository(db)
            docs = await doc_repo.list_by_kb(kb_id)

        results = []
        for doc in docs:
            if not doc.content:
                continue
            lines = doc.content.split("\n")
            for i, line in enumerate(lines):
                if query_lower in line.lower():
                    start = max(0, i - 1)
                    end = min(len(lines), i + 2)
                    results.append({
                        "filename": doc.filename,
                        "line_number": i + 1,
                        "snippet": "\n".join(lines[start:end])[:300],
                    })
                    if len(results) >= 20:
                        break
            if len(results) >= 20:
                break

        return {"results": results, "query": query, "count": len(results)}
