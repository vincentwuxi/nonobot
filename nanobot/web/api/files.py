"""Files API — sandbox file management."""

from __future__ import annotations

from datetime import datetime

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse

from nanobot.web.shared import _sandbox_root, _safe_path


async def api_files_list(request: Request) -> JSONResponse:
    """List files in sandbox directory."""
    rel = request.query_params.get("path", ".")
    target = _safe_path(rel)
    if not target:
        return JSONResponse({"error": "path outside sandbox"}, status_code=403)
    if not target.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    if not target.is_dir():
        return JSONResponse({"error": "not a directory"}, status_code=400)

    items = []
    for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        stat = item.stat()
        items.append({
            "name": item.name,
            "type": "dir" if item.is_dir() else "file",
            "size": stat.st_size if item.is_file() else None,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "path": str(item.relative_to(_sandbox_root().resolve())),
        })
    return JSONResponse({
        "path": rel,
        "items": items,
        "sandbox": str(_sandbox_root()),
    })


async def api_files_upload(request: Request) -> JSONResponse:
    """Upload file to sandbox."""
    form = await request.form()
    upload_file = form.get("file")
    dest_dir = form.get("path", "uploads")

    if not upload_file:
        return JSONResponse({"error": "no file"}, status_code=400)

    target_dir = _safe_path(dest_dir)
    if not target_dir:
        return JSONResponse({"error": "path outside sandbox"}, status_code=403)
    target_dir.mkdir(parents=True, exist_ok=True)

    dest = target_dir / upload_file.filename
    content = await upload_file.read()
    dest.write_bytes(content)

    return JSONResponse({
        "uploaded": upload_file.filename,
        "size": len(content),
        "path": str(dest.relative_to(_sandbox_root().resolve())),
    })


async def api_files_download(request: Request) -> FileResponse:
    """Download a file from sandbox."""
    rel = request.query_params.get("path", "")
    if not rel:
        return JSONResponse({"error": "path required"}, status_code=400)
    target = _safe_path(rel)
    if not target:
        return JSONResponse({"error": "path outside sandbox"}, status_code=403)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)
    return FileResponse(str(target), filename=target.name)


async def api_files_delete(request: Request) -> JSONResponse:
    """Delete a file or empty directory."""
    data = await request.json()
    rel = data.get("path", "")
    if not rel:
        return JSONResponse({"error": "path required"}, status_code=400)
    target = _safe_path(rel)
    if not target:
        return JSONResponse({"error": "path outside sandbox"}, status_code=403)
    if not target.exists():
        return JSONResponse({"error": "not found"}, status_code=404)

    if target.is_file():
        target.unlink()
    elif target.is_dir():
        import shutil
        shutil.rmtree(target)
    return JSONResponse({"deleted": rel})


async def api_files_mkdir(request: Request) -> JSONResponse:
    """Create a directory."""
    data = await request.json()
    rel = data.get("path", "")
    if not rel:
        return JSONResponse({"error": "path required"}, status_code=400)
    target = _safe_path(rel)
    if not target:
        return JSONResponse({"error": "path outside sandbox"}, status_code=403)
    target.mkdir(parents=True, exist_ok=True)
    return JSONResponse({"created": rel})
