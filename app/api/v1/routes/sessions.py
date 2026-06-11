import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import LOGGER, get_current_user_id
from app.core.config import CONTEXT_FILE, SUPPORTED_ATTACHMENT_EXTS
from app.extractors.manager import _extract_attachment_excerpt
from app.models.chat import ChatSession
from app.models.schema import (
    CreateSessionRequest,
    SessionResponse,
    SessionSummaryResponse,
    UpdateSessionSettingsRequest,
)
from app.services.context_service import load_default_context_messages, load_materials_context_messages
from app.services.session_service import SESSIONS, _session_history_for_client, _session_summary_for_client

router = APIRouter()


@router.post("/sessions", response_model=SessionResponse)
async def create_session(
    req: CreateSessionRequest,
    user_id: str = Depends(get_current_user_id),
) -> SessionResponse:
    sid = uuid.uuid4().hex
    session = ChatSession(
        session_id=sid,
        show_context_in_history=req.show_context_in_history,
        context_file=CONTEXT_FILE.name,
        user_id=user_id,
    )
    session.messages.extend(load_default_context_messages(CONTEXT_FILE, LOGGER))
    session.messages.extend(
        load_materials_context_messages(
            supported_attachment_exts=SUPPORTED_ATTACHMENT_EXTS,
            extract_attachment_excerpt=_extract_attachment_excerpt,
            logger=LOGGER,
        )
    )
    SESSIONS[sid] = session
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
) -> list[dict[str, str]]:
    summaries = [
        _session_summary_for_client(session)
        for session in SESSIONS.values()
        if session.user_id == user_id
    ]
    return sorted(summaries, key=lambda item: item["updated_at"], reverse=True)


@router.patch("/sessions/{session_id}/settings", response_model=SessionResponse)
async def update_session_settings(
    session_id: str,
    req: UpdateSessionSettingsRequest,
    user_id: str = Depends(get_current_user_id),
) -> SessionResponse:
    session = SESSIONS.get(session_id)
    if not session or session.user_id != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    session.show_context_in_history = req.show_context_in_history
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
) -> SessionResponse:
    session = SESSIONS.get(session_id)
    if not session or session.user_id != user_id:
        raise HTTPException(status_code=404, detail="会话不存在")
    return SessionResponse(
        session_id=session.session_id,
        show_context_in_history=session.show_context_in_history,
        created_at=session.created_at,
        history=_session_history_for_client(session),
    )
