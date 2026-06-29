from collections.abc import AsyncIterator
import json
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LOGGER, get_current_user_id
from app.core.config import BASE_DIR, settings
from app.core.database import get_db
from app.extractors.manager import _save_attachments
from app.models.chat import ChatMessage, ChatSession
from app.services.chat_service import _append_user_message_with_attachments, _finalize_stream_reply
from app.services.context_service import load_master_messages
from app.services.llm_service import _build_model_messages, _call_llm, _call_llm_stream
from app.services.message_service import append_message
from app.services.session_service import SESSION_CACHE, _session_history_for_client, get_session_by_id, rebuild_memory_session, update_session_template
from app.services.sse_service import build_delta_event, build_error_event, format_sse_event
from app.services.template_prompt_service import resolve_master

router = APIRouter()


_LLM_PAYLOAD_DEBUG = settings.llm_payload_debug
_LLM_PAYLOAD_PREVIEW_CHARS = max(settings.llm_payload_preview_chars, 50)


def _sanitize_preview(text: str, limit: int) -> str:
    if not text:
        return ""
    compact = text.replace("\n", "\\n")
    return compact[:limit]


def _log_llm_payload_debug(logger, llm_messages: list[dict[str, str]], user_msg: ChatMessage) -> None:
    if not _LLM_PAYLOAD_DEBUG:
        return

    total_chars = sum(len(m.get("content") or "") for m in llm_messages)
    final_user_content = ""
    if llm_messages and llm_messages[-1].get("role") == "user":
        final_user_content = llm_messages[-1].get("content") or ""

    attachment_stats: list[dict[str, Any]] = []
    for att in user_msg.attachments:
        excerpt = att.get("excerpt") or ""
        attachment_stats.append(
            {
                "filename": att.get("filename"),
                "size": att.get("size"),
                "excerpt_chars": len(excerpt),
            }
        )

    logger.info(
        "llm_payload_debug messages={} total_chars={} final_user_chars={} attachment_stats={} user_head='{}' user_tail='{}'",
        len(llm_messages),
        total_chars,
        len(final_user_content),
        json.dumps(attachment_stats, ensure_ascii=False),
        _sanitize_preview(final_user_content, _LLM_PAYLOAD_PREVIEW_CHARS),
        _sanitize_preview(final_user_content[-_LLM_PAYLOAD_PREVIEW_CHARS:], _LLM_PAYLOAD_PREVIEW_CHARS),
    )


async def _get_or_load_session(session_id: str, user_id: str, db: AsyncSession | None) -> ChatSession | None:
    """Look up session from cache, falling back to DB when available."""
    session = SESSION_CACHE.get(session_id)
    if session and session.user_id == user_id:
        return session
    # Try DB fallback
    if db is not None:
        try:
            session_db = await get_session_by_id(db=db, session_id=session_id, user_id=user_id)
            if session_db:
                session = rebuild_memory_session(session_db)
                SESSION_CACHE[session_id] = session
                return session
        except Exception:
            pass
    return None


async def _resolve_master_messages(
    user_msg: ChatMessage,
    logger,
    current_template_id: str | None = None,
) -> tuple[list[ChatMessage], str | None]:
    """Resolve the master prompt for this turn. Raises HTTPException(400) on
    intercept or empty-master-load. Returns (master_messages, new_template_id)
    where new_template_id is the D1..D7 to persist (None for _generic)."""
    resolution = await resolve_master(
        user_msg.attachments, BASE_DIR, current_template_id
    )
    if resolution.status == "intercept":
        logger.info("template_intercept reason={}", resolution.reason)
        raise HTTPException(status_code=400, detail=resolution.intercept_message)
    master_messages = load_master_messages(resolution.master_path, logger)
    if not master_messages:
        logger.error("master_load_empty path={}", resolution.master_path)
        raise HTTPException(
            status_code=400, detail="模板母版加载失败，请联系管理员"
        )
    return master_messages, resolution.document_id


@router.post("/chat")
async def chat(
    session_id: str = Form(...),
    message: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    session = await _get_or_load_session(session_id, user_id, db)
    if not session:
        raise HTTPException(status_code=404, detail="\u4f1a\u8bdd\u4e0d\u5b58\u5728")

    chat_logger = LOGGER.bind(session_id=session_id)
    user_msg = await _append_user_message_with_attachments(
        session=session,
        session_id=session_id,
        db=db,
        message=message,
        files=files,
        save_attachments=_save_attachments,
        request_logger=chat_logger,
        log_event="chat_request_received attachments={} has_text={}",
    )

    master_messages, new_template = await _resolve_master_messages(
        user_msg, chat_logger, session.current_template_id
    )
    if new_template and new_template != session.current_template_id:
        session.current_template_id = new_template
        if db is not None:
            await update_session_template(db, session_id, new_template)
    llm_messages = _build_model_messages(session, user_msg, master_messages)
    _log_llm_payload_debug(chat_logger, llm_messages, user_msg)
    assistant_text = await _call_llm(llm_messages)

    assistant_msg = ChatMessage(role="assistant", content=assistant_text)
    session.messages.append(assistant_msg)
    chat_logger.info("chat_reply_generated reply_chars={}", len(assistant_text))

    # Persist assistant reply to DB when available
    if db is not None:
        await append_message(
            db=db, session_id=session_id, role="assistant",
            content=assistant_text,
        )

    return {
        "session_id": session.session_id,
        "reply": assistant_text,
        "history": _session_history_for_client(session),
    }


@router.post("/chat/stream")
async def chat_stream(
    session_id: str = Form(...),
    message: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    session = await _get_or_load_session(session_id, user_id, db)
    if not session:
        raise HTTPException(status_code=404, detail="\u4f1a\u8bdd\u4e0d\u5b58\u5728")

    stream_logger = LOGGER.bind(session_id=session_id)
    user_msg = await _append_user_message_with_attachments(
        session=session,
        session_id=session_id,
        db=db,
        message=message,
        files=files,
        save_attachments=_save_attachments,
        request_logger=stream_logger,
        log_event="chat_stream_request_received attachments={} has_text={}",
    )
    master_messages, new_template = await _resolve_master_messages(
        user_msg, stream_logger, session.current_template_id
    )
    if new_template and new_template != session.current_template_id:
        session.current_template_id = new_template
        if db is not None:
            await update_session_template(db, session_id, new_template)
    llm_messages = _build_model_messages(session, user_msg, master_messages)
    _log_llm_payload_debug(stream_logger, llm_messages, user_msg)

    async def event_gen() -> AsyncIterator[str]:
        chunks: list[str] = []
        try:
            async for delta in _call_llm_stream(llm_messages):
                chunks.append(delta)
                yield build_delta_event(delta)
        except HTTPException as exc:
            stream_logger.warning("chat_stream_http_error detail={}", exc.detail)
            yield build_error_event(str(exc.detail))
            return
        except Exception as exc:  # pragma: no cover - defensive branch
            stream_logger.exception("chat_stream_unexpected_error")
            yield build_error_event(f"\u6d41\u5f0f\u8f93\u51fa\u5931\u8d25: {exc}")
            return

        done_payload = _finalize_stream_reply(
            session=session,
            session_id=session_id,
            db=db,
            chunks=chunks,
            stream_logger=stream_logger,
        )
        # Persist assistant reply to DB when available
        if db is not None:
            await append_message(
                db=db, session_id=session_id, role="assistant",
                content=done_payload["reply"],
            )
        yield format_sse_event(done_payload)

    return StreamingResponse(event_gen(), media_type="text/event-stream")
