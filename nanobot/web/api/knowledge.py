"""Knowledge Base API — thin controller using KnowledgeService."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from nanobot.services.knowledge_service import KnowledgeService


async def kb_list(request: Request) -> JSONResponse:
    """List all knowledge bases."""
    return JSONResponse(await KnowledgeService.list_all())


async def kb_create(request: Request) -> JSONResponse:
    """Create a new knowledge base."""
    user = getattr(request.state, "user", {})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    try:
        kb_id = await KnowledgeService.create(body, user=user)
        return JSONResponse({"ok": True, "id": kb_id})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


async def kb_get(request: Request) -> JSONResponse:
    """Get a knowledge base with its documents."""
    result = await KnowledgeService.get_with_documents(request.path_params["id"])
    if not result:
        return JSONResponse({"error": "knowledge base not found"}, status_code=404)
    return JSONResponse(result)


async def kb_delete(request: Request) -> JSONResponse:
    """Delete a knowledge base."""
    user = getattr(request.state, "user", {})
    ok = await KnowledgeService.delete(request.path_params["id"], user=user)
    if not ok:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({"ok": True})


async def kb_upload_document(request: Request) -> JSONResponse:
    """Upload a document to a knowledge base."""
    user = getattr(request.state, "user", {})
    kb_id = request.path_params["id"]

    # Handle multipart form upload
    form = await request.form()
    uploaded = form.get("file")

    if uploaded:
        content_bytes = await uploaded.read()
        filename = getattr(uploaded, "filename", "unnamed.txt") or "unnamed.txt"
        content = content_bytes.decode("utf-8", errors="replace")
    else:
        # Fallback: JSON body with direct text
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "file or JSON body required"}, status_code=400)
        content = body.get("content", "")
        filename = body.get("filename", "untitled.md")

    result = await KnowledgeService.upload_document(kb_id, filename, content, user=user)
    if isinstance(result, str):
        return JSONResponse({"error": result}, status_code=404)
    return JSONResponse({"ok": True, **result})


async def kb_delete_document(request: Request) -> JSONResponse:
    """Delete a document from a knowledge base."""
    user = getattr(request.state, "user", {})
    ok = await KnowledgeService.delete_document(
        request.path_params["id"], request.path_params["doc_id"], user=user,
    )
    if not ok:
        return JSONResponse({"error": "document not found"}, status_code=404)
    return JSONResponse({"ok": True})


async def kb_get_content(request: Request) -> JSONResponse:
    """Get concatenated content of all KB documents."""
    return JSONResponse(await KnowledgeService.get_content(request.path_params["id"]))


async def kb_search(request: Request) -> JSONResponse:
    """Search documents in a knowledge base."""
    query = request.query_params.get("q", "").strip()
    return JSONResponse(await KnowledgeService.search(request.path_params["id"], query))
