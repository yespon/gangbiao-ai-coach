import asyncio

from fastapi import APIRouter
from sqlalchemy import text

from app.core.database import engine

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Health check with PostgreSQL connectivity verification."""
    try:
        async with asyncio.timeout(3):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "degraded", "detail": f"db_unavailable: {exc}"}
