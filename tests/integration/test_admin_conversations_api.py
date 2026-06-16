import uuid

import main
from app.api.deps import get_current_user, get_db
from app.models.db_models import ManagedUserDB, User


def _coach_user():
    profile = ManagedUserDB(id=uuid.uuid4(), primary_role="coach", is_coach=True)
    user = User()
    user.id = uuid.uuid4()
    user.email = "coach@example.com"
    user.is_active = True
    user.is_admin = False
    user.managed_user = profile
    user.managed_user_id = profile.id
    return user


def test_conversation_users_route_returns_service_payload(client, monkeypatch):
    async def fake_list(db, user, scope):
        return [{"managed_user_id": "student-id", "employee_no": "1001", "session_count": 2}]

    monkeypatch.setattr("app.api.v1.routes.admin.list_conversation_students", fake_list)
    main.app.dependency_overrides[get_current_user] = _coach_user
    main.app.dependency_overrides[get_db] = lambda: object()
    resp = client.get("/api/v1/admin/conversations/users?scope=mine")
    assert resp.status_code == 200
    assert resp.json() == [{"managed_user_id": "student-id", "employee_no": "1001", "session_count": 2}]


def test_conversation_user_sessions_route_returns_service_payload(client, monkeypatch):
    managed_user_id = uuid.uuid4()

    async def fake_sessions(db, user, target_managed_user_id):
        assert target_managed_user_id == managed_user_id
        return [{"session_id": "session-id", "message_count": 3}]

    monkeypatch.setattr("app.api.v1.routes.admin.list_student_sessions", fake_sessions)
    main.app.dependency_overrides[get_current_user] = _coach_user
    main.app.dependency_overrides[get_db] = lambda: object()
    resp = client.get(f"/api/v1/admin/conversations/users/{managed_user_id}/sessions")
    assert resp.status_code == 200
    assert resp.json() == [{"session_id": "session-id", "message_count": 3}]


def test_conversation_session_route_returns_readonly_detail(client, monkeypatch):
    session_id = uuid.uuid4()

    async def fake_detail(db, user, target_session_id):
        assert target_session_id == session_id
        return {"session_id": str(session_id), "history": [{"role": "user", "content": "hello"}]}

    monkeypatch.setattr("app.api.v1.routes.admin.get_conversation_session", fake_detail)
    main.app.dependency_overrides[get_current_user] = _coach_user
    main.app.dependency_overrides[get_db] = lambda: object()
    resp = client.get(f"/api/v1/admin/conversations/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["history"] == [{"role": "user", "content": "hello"}]
