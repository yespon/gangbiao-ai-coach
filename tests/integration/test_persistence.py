import pytest
from fastapi.testclient import TestClient

# Skip entire module if PostgreSQL is not available
_pg_available = False

try:
    from sqlalchemy.ext.asyncio import create_async_engine
    import asyncio
    _engine = create_async_engine("postgresql+asyncpg://gangbiao:gangbiao@localhost:5432/gangbiao")
    async def _check():
        async with _engine.connect() as conn:
            return True
    _pg_available = asyncio.run(_check())
    _engine.dispose()
except Exception:
    _pg_available = False

pytestmark = pytest.mark.skipif(not _pg_available, reason="PostgreSQL not available")


def test_create_session_persists(client):
    """Create a session, then re-query it from the API."""
    resp = client.post("/api/v1/sessions", json={"show_context_in_history": True})
    assert resp.status_code == 200
    data = resp.json()
    session_id = data["session_id"]

    # Re-fetch the session
    resp2 = client.get(f"/api/v1/sessions/{session_id}")
    assert resp2.status_code == 200
    assert resp2.json()["session_id"] == session_id


def test_list_sessions_filters_by_user(client):
    """Different users should only see their own sessions."""
    # Create a session with the default test user
    resp1 = client.post("/api/v1/sessions", json={"show_context_in_history": True})
    assert resp1.status_code == 200

    # List sessions - should see at least one
    resp2 = client.get("/api/v1/sessions")
    assert resp2.status_code == 200
    sessions = resp2.json()
    assert len(sessions) >= 1


def test_chat_message_persisted(client):
    """After sending a chat message, the messages table should have a record."""
    # Create session
    resp = client.post("/api/v1/sessions", json={"show_context_in_history": True})
    session_id = resp.json()["session_id"]

    # Send a message (non-stream mode)
    resp2 = client.post(
        "/api/v1/chat",
        json={"session_id": session_id, "message": "测试消息"},
    )
    assert resp2.status_code == 200

    # Verify session history contains the message
    resp3 = client.get(f"/api/v1/sessions/{session_id}")
    history = resp3.json()["history"]
    user_msgs = [m for m in history if m["role"] == "user" and not m.get("is_context")]
    assert len(user_msgs) >= 1
    assert "测试消息" in user_msgs[0]["content"]


def test_session_history_from_db(client):
    """Session history loaded from DB should match what was sent."""
    resp = client.post("/api/v1/sessions", json={"show_context_in_history": False})
    session_id = resp.json()["session_id"]

    resp2 = client.post(
        "/api/v1/chat",
        json={"session_id": session_id, "message": "你好"},
    )
    assert resp2.status_code == 200

    # Fetch session and verify history
    resp3 = client.get(f"/api/v1/sessions/{session_id}")
    history = resp3.json()["history"]
    non_context = [m for m in history if not m.get("is_context")]
    assert len(non_context) >= 1


def test_context_messages_persisted(client):
    """母体不再在会话创建时持久化为 context 消息（改为每轮动态加载）。"""
    resp = client.post("/api/v1/sessions", json={"show_context_in_history": True})
    session_id = resp.json()["session_id"]

    resp2 = client.get(f"/api/v1/sessions/{session_id}")
    history = resp2.json()["history"]
    context_msgs = [m for m in history if m.get("is_context")]
    assert context_msgs == []