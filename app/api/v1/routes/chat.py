from collections.abc import AsyncIterator
import json
import os
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.api.deps import LOGGER
from app.extractors.manager import _save_attachments
from app.models.chat import ChatMessage
from app.services.chat_service import _append_user_message_with_attachments, _finalize_stream_reply
from app.services.llm_service import _build_model_messages, _call_llm, _call_llm_stream
from app.services.session_service import SESSIONS, _session_history_for_client
from app.services.sse_service import build_delta_event, build_error_event, format_sse_event

router = APIRouter()


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


_LLM_PAYLOAD_DEBUG = _env_flag("LLM_PAYLOAD_DEBUG", False)
_LLM_PAYLOAD_PREVIEW_CHARS = max(int(os.getenv("LLM_PAYLOAD_PREVIEW_CHARS", "180") or "180"), 50)


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


@router.post("/chat")
async def chat(
    session_id: str = Form(...),
    message: str = Form(...),
    files: list[UploadFile] = File(default=[]),
) -> dict[str, Any]:
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    chat_logger = LOGGER.bind(session_id=session_id)
    user_msg = await _append_user_message_with_attachments(
        session=session,
        session_id=session_id,
        message=message,
        files=files,
        save_attachments=_save_attachments,
        request_logger=chat_logger,
        log_event="chat_request_received attachments={} has_text={}",
    )

    llm_messages = _build_model_messages(session, user_msg)
    _log_llm_payload_debug(chat_logger, llm_messages, user_msg)
    assistant_text = await _call_llm(llm_messages)

    assistant_msg = ChatMessage(role="assistant", content=assistant_text)
    session.messages.append(assistant_msg)
    chat_logger.info("chat_reply_generated reply_chars={}", len(assistant_text))

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
) -> StreamingResponse:
    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    stream_logger = LOGGER.bind(session_id=session_id)
    user_msg = await _append_user_message_with_attachments(
        session=session,
        session_id=session_id,
        message=message,
        files=files,
        save_attachments=_save_attachments,
        request_logger=stream_logger,
        log_event="chat_stream_request_received attachments={} has_text={}",
    )
    llm_messages = _build_model_messages(session, user_msg)
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
            yield build_error_event(f"流式输出失败: {exc}")
            return

        done_payload = _finalize_stream_reply(
            session=session,
            chunks=chunks,
            stream_logger=stream_logger,
        )
        yield format_sse_event(done_payload)

    return StreamingResponse(event_gen(), media_type="text/event-stream")
