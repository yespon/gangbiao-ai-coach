import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.router import legacy_api_router
from app.api.v1.router import api_v1_router
from app.core.config import get_cors_allow_origin_regex, get_cors_allow_origins, settings, UPLOAD_ROOT
from app.core.database import async_session_factory
from app.core.logger import attach_request_logging_middleware, get_component_logger, setup_logging
from app.services.cas_service import cleanup_expired_sessions
from app.services.context_service import preload_master_messages
from app.services.template_prompt_service import iter_master_entries, validate_master_registry

setup_logging()

LOGGER = get_component_logger(component="chatbot")


async def _session_cleanup_loop():
    """Background task: periodically clean up expired auth sessions."""
    while True:
        await asyncio.sleep(settings.session_cleanup_interval_minutes * 60)
        try:
            async with async_session_factory() as db:
                deleted = await cleanup_expired_sessions(
                    db, settings.session_cleanup_grace_days
                )
                if deleted:
                    LOGGER.info(f"Session cleanup: removed {deleted} expired sessions")
        except Exception:
            LOGGER.exception("Session cleanup failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: start background tasks, yield, then cancel."""
    cleanup_task = asyncio.create_task(_session_cleanup_loop())
    validate_master_registry(LOGGER)
    preload_master_messages(iter_master_entries(), LOGGER)
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Gangbiao Chatbot", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_allow_origins(),
    allow_origin_regex=get_cors_allow_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

attach_request_logging_middleware(app, LOGGER)
app.include_router(legacy_api_router, prefix="/api")
app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/")
async def index() -> dict[str, str]:
    return {
        "service": "gangbiao-chatbot-api",
        "status": "ok",
        "version": "0.1.0",
    }


@app.get("/uploads/{rest_of_path:path}")
async def serve_upload(rest_of_path: str):
    """Serve feedback uploads. Any path outside /uploads/feedback/... is rejected."""
    if not rest_of_path.startswith("feedback/"):
        raise HTTPException(status_code=404, detail="not_found")
    target = (UPLOAD_ROOT / rest_of_path).resolve()
    feedback_root = (UPLOAD_ROOT / "feedback").resolve()
    # Prevent path traversal: target must be strictly inside feedback_root
    try:
        target.relative_to(feedback_root)
    except ValueError:
        raise HTTPException(status_code=404, detail="not_found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="not_found")
    return FileResponse(str(target))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=2024, reload=True)
