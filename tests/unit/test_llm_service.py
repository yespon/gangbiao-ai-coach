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


def test_build_model_messages_includes_attachment_excerpt_in_current_user_payload():
    session = ChatSession(
        session_id="s1",
        show_context_in_history=False,
        context_file="ctx.json",
    )
    user_msg = ChatMessage(
        role="user",
        content="请分析附件",
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

    assert "请分析附件" in current_user
    assert "文件名: demo.xlsx" in current_user
    assert "内容:\n任务A\t价值A\t目的A\t成果A" in current_user
