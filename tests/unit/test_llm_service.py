import os

import pytest

from app.models.chat import ChatMessage, ChatSession
from app.services.llm_service import _build_model_messages


def _make_session_with_context(n_context_turns: int, n_real_turns: int = 0) -> tuple[ChatSession, ChatMessage]:
    session = ChatSession(session_id="s1", show_context_in_history=False, context_file="ctx.json")
    for i in range(n_context_turns):
        session.messages.append(ChatMessage(role="user", content=f"ctx_user_{i}", is_context=True))
        session.messages.append(ChatMessage(role="assistant", content=f"ctx_asst_{i}", is_context=True))
    for i in range(n_real_turns):
        session.messages.append(ChatMessage(role="user", content=f"real_user_{i}"))
        session.messages.append(ChatMessage(role="assistant", content=f"real_asst_{i}"))
    user_msg = ChatMessage(role="user", content="当前问题", attachments=[{"filename": "a.xlsx", "size": 1, "excerpt": "附件内容"}])
    session.messages.append(user_msg)
    return session, user_msg


def test_build_model_messages_skips_duplicate_current_user_message():
    session = ChatSession(session_id="s1", show_context_in_history=False, context_file="ctx.json")
    old_user = ChatMessage(role="user", content="历史问题")
    user_msg = ChatMessage(role="user", content="当前问题")
    session.messages.extend([old_user, user_msg])

    messages = _build_model_messages(session, user_msg)
    user_payloads = [m["content"] for m in messages if m["role"] == "user"]

    assert len(user_payloads) == 2
    assert "历史问题" in user_payloads[0]
    assert "当前问题" in user_payloads[1]


def test_build_model_messages_includes_attachment_excerpt_in_current_user_payload():
    session = ChatSession(session_id="s1", show_context_in_history=False, context_file="ctx.json")
    user_msg = ChatMessage(
        role="user",
        content="请分析附件",
        attachments=[{"filename": "demo.xlsx", "size": 123, "excerpt": "任务A\t价值A\t目的A\t成果A"}],
    )
    session.messages.append(user_msg)

    messages = _build_model_messages(session, user_msg)
    current_user = messages[-1]["content"]

    assert "请分析附件" in current_user
    assert "文件名: demo.xlsx" in current_user
    assert "内容:\n任务A\t价值A\t目的A\t成果A" in current_user


def test_build_model_messages_trims_context_when_over_budget(monkeypatch):
    monkeypatch.setattr("app.services.llm_service._LLM_MAX_HISTORY_CHARS", 500)
    session, user_msg = _make_session_with_context(n_context_turns=50)

    messages = _build_model_messages(session, user_msg)

    total_chars = sum(len(m.get("content", "")) for m in messages)
    # Should be well under or just around budget (system + current user + some context)
    assert total_chars <= 500 + len(user_msg.content) + 200  # some slack for system/user
    # Current user message must always be last
    assert messages[-1]["role"] == "user"
    assert "当前问题" in messages[-1]["content"]
    assert "附件内容" in messages[-1]["content"]


def test_build_model_messages_real_turns_never_trimmed(monkeypatch):
    monkeypatch.setattr("app.services.llm_service._LLM_MAX_HISTORY_CHARS", 300)
    session, user_msg = _make_session_with_context(n_context_turns=50, n_real_turns=2)

    messages = _build_model_messages(session, user_msg)

    contents = [m["content"] for m in messages]
    # Real conversation turns must always survive trimming
    assert any("real_user_0" in c for c in contents)
    assert any("real_asst_0" in c for c in contents)


def test_build_model_messages_no_limit_when_zero(monkeypatch):
    monkeypatch.setattr("app.services.llm_service._LLM_MAX_HISTORY_CHARS", 0)
    session, user_msg = _make_session_with_context(n_context_turns=10)

    messages = _build_model_messages(session, user_msg)

    context_contents = [m["content"] for m in messages if m["role"] in {"user", "assistant"} and "ctx_" in m.get("content", "")]
    assert len(context_contents) == 20  # 10 pairs × 2
