from app.models.chat import ChatMessage, ChatSession
from app.services.llm_service import _build_model_messages


def test_build_model_messages_skips_duplicate_current_user_message():
    session = ChatSession(
        session_id="s1",
        show_context_in_history=False,
        context_file="ctx.json",
    )
    old_user = ChatMessage(role="user", content="历史问题")
    user_msg = ChatMessage(role="user", content="当前问题")
    session.messages.extend([old_user, user_msg])

    messages = _build_model_messages(session, user_msg)
    user_payloads = [m["content"] for m in messages if m["role"] == "user"]

    assert len(user_payloads) == 2
    assert "历史问题" in user_payloads[0]
    assert "当前问题" in user_payloads[1]


def test_build_model_messages_passes_user_content_verbatim():
    """Attachment excerpts are embedded in content by chat_service (origin behavior).
    _build_model_messages should pass user_msg.content through unchanged."""
    session = ChatSession(
        session_id="s1",
        show_context_in_history=False,
        context_file="ctx.json",
    )
    # Simulate origin behavior: file hint already concatenated into content
    content_with_hint = (
        "请分析附件\n\n"
        "附件: demo.xlsx (123 bytes)\n"
        "可读摘要:\n任务A\t价值A\t目的A\t成果A"
    )
    user_msg = ChatMessage(
        role="user",
        content=content_with_hint,
        attachments=[
            {
                "filename": "demo.xlsx",
                "size": 123,
                "excerpt": "任务A\t价值A\t目的A\t成果A",
            }
        ],
    )
    session.messages.append(user_msg)

    messages = _build_model_messages(session, user_msg)
    current_user = messages[-1]["content"]

    assert current_user == content_with_hint, "content should be passed through verbatim"


def _session():
    s = ChatSession(session_id="s1", show_context_in_history=False, context_file="ctx.json")
    s.messages.append(ChatMessage(role="user", content="历史问题"))
    return s


def test_build_model_messages_with_master_prefix_replaces_system_line():
    session = _session()
    user_msg = ChatMessage(role="user", content="当前问题")
    session.messages.append(user_msg)

    master = [
        ChatMessage(role="system", content="母版system", is_context=True),
        ChatMessage(role="user", content="母版user", is_context=True),
        ChatMessage(role="assistant", content="母版assistant", is_context=True),
    ]
    msgs = _build_model_messages(session, user_msg, master_messages=master)

    # master messages come first, verbatim
    assert msgs[0] == {"role": "system", "content": "母版system"}
    assert msgs[1] == {"role": "user", "content": "母版user"}
    assert msgs[2] == {"role": "assistant", "content": "母版assistant"}
    # hardcoded system line must NOT appear
    assert "岗位标准化 AI 教练" not in "".join(m["content"] for m in msgs)
    # current user msg is last
    assert msgs[-1] == {"role": "user", "content": "当前问题"}


def test_build_model_messages_none_master_falls_back_to_hardcoded_system():
    session = _session()
    user_msg = ChatMessage(role="user", content="当前问题")
    session.messages.append(user_msg)

    msgs = _build_model_messages(session, user_msg, master_messages=None)
    assert msgs[0]["role"] == "system"
    assert "岗位标准化 AI 教练" in msgs[0]["content"]
