from typing import Any

from fastapi import HTTPException, UploadFile

from app.models.chat import ChatMessage, ChatSession
from app.services.session_service import _session_history_for_client


async def _append_user_message_with_attachments(
    *,
    session: ChatSession,
    session_id: str,
    message: str,
    files: list[UploadFile],
    save_attachments,
    request_logger,
    log_event: str,
) -> ChatMessage:
    user_text = message.strip()
    if not user_text and not files:
        raise HTTPException(status_code=400, detail="消息和附件不能同时为空")

    saved_files, file_hints = await save_attachments(session_id=session_id, files=files)
    request_logger.info(
        log_event,
        len(saved_files),
        bool(user_text),
    )
    final_user_text = user_text
    if file_hints:
        final_user_text = f"{user_text}\n\n" + "\n\n".join(file_hints)
    user_msg = ChatMessage(role="user", content=final_user_text, attachments=saved_files)
    session.messages.append(user_msg)
    return user_msg


def _finalize_stream_reply(
    *,
    session: ChatSession,
    chunks: list[str],
    stream_logger,
) -> dict[str, Any]:
    assistant_text = "".join(chunks).strip() or "（模型未返回内容）"
    assistant_msg = ChatMessage(role="assistant", content=assistant_text)
    session.messages.append(assistant_msg)
    stream_logger.info(
        "chat_stream_reply_generated reply_chars={} chunk_count={}",
        len(assistant_text),
        len(chunks),
    )

    return {
        "type": "done",
        "reply": assistant_text,
        "history": _session_history_for_client(session),
    }
