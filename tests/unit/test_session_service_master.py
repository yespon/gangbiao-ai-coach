import pytest
from unittest.mock import AsyncMock, MagicMock

from app.models.chat import ChatSession, ChatMessage
from app.services import session_service as ss
from app.services.session_service import update_session_template, rebuild_memory_session


def test_rebuild_memory_session_backfills_current_template_id():
    """rebuild_memory_session (DB→memory) must carry current_template_id."""
    session_db = MagicMock()
    session_db.id = "sid-1"
    session_db.show_context = False
    session_db.context_file = "D5.history.json"
    session_db.user_id = "u1"
    session_db.created_at = None
    session_db.current_template_id = "D5"
    session_db.messages = []

    mem = rebuild_memory_session(session_db)
    assert mem.current_template_id == "D5"
    assert isinstance(mem, ChatSession)


def test_rebuild_memory_session_current_template_none_when_unset():
    """A session with NULL current_template_id rebuilds to None."""
    session_db = MagicMock()
    session_db.id = "sid-2"
    session_db.show_context = True
    session_db.context_file = ""
    session_db.user_id = "u2"
    session_db.created_at = None
    session_db.current_template_id = None
    session_db.messages = []

    mem = rebuild_memory_session(session_db)
    assert mem.current_template_id is None


@pytest.mark.asyncio
async def test_update_session_template_executes_update_and_commits(monkeypatch):
    """update_session_template issues an UPDATE on ChatSessionDB and commits."""
    db = AsyncMock()
    # Stub sa_update chain: .where(...).values(...)
    fake_stmt = MagicMock()
    fake_where = MagicMock()
    fake_values = MagicMock()
    fake_stmt.where.return_value = fake_where
    fake_where.values.return_value = fake_values

    import app.services.session_service as svc_mod
    monkeypatch.setattr(svc_mod, "sa_update", lambda model: fake_stmt)

    await update_session_template(db, "sid-3", "D4")

    fake_stmt.where.assert_called_once()
    fake_where.values.assert_called_once_with(current_template_id="D4")
    db.execute.assert_awaited_once_with(fake_values)
    db.commit.assert_awaited_once()
