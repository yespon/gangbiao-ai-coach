import pytest
from fastapi import HTTPException

from app.models.chat import ChatSession
from app.services.chat_service import _append_user_message_with_attachments, _finalize_stream_reply


class _DummyLogger:
    def __init__(self):
        self.events: list[tuple] = []

    def info(self, msg, *args):
        self.events.append((msg, args))


@pytest.mark.asyncio
async def test_append_user_message_rejects_empty_message_without_files():
    session = ChatSession(
        session_id="s1",
        show_context_in_history=False,
        context_file="ctx.json",
    )

    async def _fake_save_attachments(*, session_id, files):
        return [], []

    with pytest.raises(HTTPException) as exc:
        await _append_user_message_with_attachments(
            session=session,
            session_id="s1",
            message="   ",
            files=[],
            save_attachments=_fake_save_attachments,
            request_logger=_DummyLogger(),
            log_event="chat_request_received attachments={} has_text={}"
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_append_user_message_includes_attachment_hints_and_logs():
    session = ChatSession(
        session_id="s1",
        show_context_in_history=False,
        context_file="ctx.json",
    )

    async def _fake_save_attachments(*, session_id, files):
        return [{"filename": "a.txt"}], ["附件: a.txt (4 bytes)\n可读摘要:\n内容"]

    logger = _DummyLogger()
    user_msg = await _append_user_message_with_attachments(
        session=session,
        session_id="s1",
        message="问题",
        files=[object()],
        save_attachments=_fake_save_attachments,
        request_logger=logger,
        log_event="chat_request_received attachments={} has_text={}"
    )

    assert user_msg.role == "user"
    assert "问题" in user_msg.content
    # file_hints are only logged, not appended to message content
    assert user_msg.attachments == [{"filename": "a.txt"}]
    assert session.messages[-1] is user_msg
    assert logger.events


def test_finalize_stream_reply_appends_assistant_and_builds_done_payload():
    session = ChatSession(
        session_id="s1",
        show_context_in_history=False,
        context_file="ctx.json",
    )
    logger = _DummyLogger()

    payload = _finalize_stream_reply(
        session=session,
        chunks=["hello", " world"],
        stream_logger=logger,
    )

    assert payload["type"] == "done"
    assert payload["reply"] == "hello world"
    assert isinstance(payload["history"], list)
    assert session.messages[-1].role == "assistant"


def test_finalize_stream_reply_uses_empty_fallback_text():
    session = ChatSession(
        session_id="s1",
        show_context_in_history=False,
        context_file="ctx.json",
    )

    payload = _finalize_stream_reply(
        session=session,
        chunks=[],
        stream_logger=_DummyLogger(),
    )

    assert payload["reply"] == "（模型未返回内容）"
