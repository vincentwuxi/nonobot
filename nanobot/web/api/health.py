"""Health check API — readiness / liveness probes."""

from __future__ import annotations

from datetime import datetime

from starlette.requests import Request
from starlette.responses import JSONResponse

from nanobot import __version__


async def health_check(request: Request) -> JSONResponse:
    """Basic liveness probe — always returns 200 if the server is up."""
    return JSONResponse({
        "status": "ok",
        "version": __version__,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    })


async def health_db(request: Request) -> JSONResponse:
    """Readiness probe — checks database connectivity."""
    try:
        from nanobot.db.engine import get_db
        from sqlalchemy import text
        async with get_db() as db:
            await db.execute(text("SELECT 1"))
        return JSONResponse({
            "status": "ok",
            "database": "connected",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })
    except Exception as e:
        return JSONResponse({
            "status": "error",
            "database": "disconnected",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }, status_code=503)
