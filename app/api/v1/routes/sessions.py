import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LOGGER, get_current_user_id
from app.core.config import CONTEXT_FILE, SUPPORTED_ATTACHMENT_EXTS
from app.core.database import get_db
from app.extractors.manager import _extract_attachment_excerpt
from app.models.chat import ChatSession
from app.models.schema import (
    CreateSessionRequest,
    SessionResponse,
    SessionSummaryResponse,
    UpdateSessionSettingsRequest,
)
from app.services.context_service import load_materials_context_messages
from app.services.session_service import (
    SESSION_CACHE,
    _session_history_for_client,
    _session_summary_for_client,
    create_session_in_db,
    list_user_sessions,
    get_session_by_id,
    update_session_settings,
    db_session_history_for_client,
    db_session_summary_for_client,
    rebuild_memory_session,
    rename_session,
    toggle_pin_session,
    soft_delete_session,
)

router = APIRouter()


@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    req: CreateSessionRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    # Hybrid mode: persist to DB when available, otherwise pure cache
    if db is not None:
        try:
            session_db = await create_session_in_db(
                db=db,
                user_id=user_id,
                show_context=req.show_context_in_history,
                context_file=CONTEXT_FILE.name,
            )
        except Exception as exc:
            LOGGER.bind(user_id=user_id).warning("create_session db_error={}", exc)
            raise HTTPException(status_code=503, detail="session_create_unavailable") from exc
        sid = str(session_db.id)
        created_at = session_db.created_at.isoformat() if session_db.created_at else ""
        show_context = session_db.show_context
        context_file = session_db.context_file or CONTEXT_FILE.name
    else:
        sid = uuid.uuid4().hex
        created_at = ""
        show_context = req.show_context_in_history
        context_file = CONTEXT_FILE.name

    # Build in-memory ChatSession for LLM runtime cache
    session = ChatSession(
        session_id=sid,
        show_context_in_history=show_context,
        context_file=context_file,
        user_id=user_id,
        created_at=created_at,
    )
    session.messages.extend(
        load_materials_context_messages(
            supported_attachment_exts=SUPPORTED_ATTACHMENT_EXTS,
            extract_attachment_excerpt=_extract_attachment_excerpt,
            logger=LOGGER,
        )
    )
    SESSION_CACHE[sid] = session

    LOGGER.bind(session_id=sid, user_id=user_id).info(
        "session_created show_context_in_history={} message_count={}",
        session.show_context_in_history,
        len(session.messages),
    )
    return SessionResponse(
        session_id=sid,
        show_context_in_history=session.show_context_in_history,
        created_at=session.created_at,
        history=_session_history_for_client(session),
    )


@router.get("/sessions", response_model=list[SessionSummaryResponse])
async def list_sessions(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, str]]:
    """List all sessions for the authenticated user.

    The DB is the source of truth. The in-memory ``SESSION_CACHE`` is
    only a runtime view used by the LLM call path; it must not be used
    to answer listing queries because (a) it is empty after every
    server restart, (b) it can contain stale or cross-user entries in
    multi-process deployments, and (c) falling back to it on an empty
    DB result masks the legitimate "no sessions yet" case as a missing
    one — which is what made history appear lost on every re-login.
    """
    if db is None:
        # No DB available (e.g. legacy cache-only test mode): fall back
        # to the in-memory cache, but only for entries that belong to
        # this user.
        summaries = [
            _session_summary_for_client(session)
            for session in SESSION_CACHE.values()
            if session.user_id == user_id
        ]
        return sorted(summaries, key=lambda item: item["updated_at"], reverse=True)

    try:
        sessions_db = await list_user_sessions(db=db, user_id=user_id)
        summaries = [db_session_summary_for_client(s) for s in sessions_db]
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        LOGGER.bind(user_id=user_id).warning(
            "list_sessions db_query_failed err={}", exc
        )
        raise HTTPException(
            status_code=503, detail="session_list_unavailable"
        ) from exc

    return sorted(summaries, key=lambda item: item["updated_at"], reverse=True)


@router.patch("/sessions/{session_id}/settings", response_model=SessionResponse)
async def update_session_settings_route(
    session_id: str,
    req: UpdateSessionSettingsRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    # Check cache first
    session = SESSION_CACHE.get(session_id)
    if not session or session.user_id != user_id:
        raise HTTPException(status_code=404, detail="\u4f1a\u8bdd\u4e0d\u5b58\u5728")

    # Update in-memory cache
    session.show_context_in_history = req.show_context_in_history

    # Update in DB when available
    if db is not None:
        session_db = await get_session_by_id(db=db, session_id=session_id, user_id=user_id)
        if session_db:
            await update_session_settings(db=db, session=session_db, show_context=req.show_context_in_history)

    return SessionResponse(
        session_id=session.session_id,
        show_context_in_history=session.show_context_in_history,
        created_at=session.created_at,
        history=_session_history_for_client(session),
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    # Check cache first
    session = SESSION_CACHE.get(session_id)
    if not session or session.user_id != user_id:
        # Try loading from DB
        if db is not None:
            session_db = await get_session_by_id(db=db, session_id=session_id, user_id=user_id)
            if session_db:
                session = rebuild_memory_session(session_db)
                SESSION_CACHE[session_id] = session
    if not session or session.user_id != user_id:
        raise HTTPException(status_code=404, detail="\u4f1a\u8bdd\u4e0d\u5b58\u5728")

    return SessionResponse(
        session_id=session.session_id,
        show_context_in_history=session.show_context_in_history,
        created_at=session.created_at,
        history=_session_history_for_client(session),
    )


# --- Session management: rename / pin / delete ---


class RenameSessionRequest(BaseModel):
    title: str


@router.patch("/sessions/{session_id}/title")
async def rename_session_route(
    session_id: str,
    body: RenameSessionRequest,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Rename a session (set custom title)."""
    if db is None:
        raise HTTPException(status_code=503, detail="database_unavailable")
    try:
        session_db = await rename_session(db, session_id, user_id, body.title)
    except Exception as exc:
        LOGGER.bind(session_id=session_id, user_id=user_id).warning(
            "rename_session db_error={}", exc
        )
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if session_db is None:
        raise HTTPException(status_code=404, detail="\u4f1a\u8bdd\u4e0d\u5b58\u5728")
    return {"session_id": str(session_db.id), "title": session_db.title}


@router.patch("/sessions/{session_id}/pin")
async def toggle_pin_session_route(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Toggle the pinned state of a session."""
    if db is None:
        raise HTTPException(status_code=503, detail="database_unavailable")
    try:
        session_db = await toggle_pin_session(db, session_id, user_id)
    except Exception as exc:
        LOGGER.bind(session_id=session_id, user_id=user_id).warning(
            "toggle_pin_session db_error={}", exc
        )
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if session_db is None:
        raise HTTPException(status_code=404, detail="\u4f1a\u8bdd\u4e0d\u5b58\u5728")
    return {"session_id": str(session_db.id), "pinned": session_db.pinned}


@router.delete("/sessions/{session_id}")
async def delete_session_route(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a session."""
    if db is None:
        raise HTTPException(status_code=503, detail="database_unavailable")
    try:
        ok = await soft_delete_session(db, session_id, user_id)
    except Exception as exc:
        LOGGER.bind(session_id=session_id, user_id=user_id).warning(
            "delete_session db_error={}", exc
        )
        raise HTTPException(status_code=503, detail="database_unavailable") from exc
    if not ok:
        raise HTTPException(status_code=404, detail="\u4f1a\u8bdd\u4e0d\u5b58\u5728")
    return {"ok": True}
