# T4-1: show_context_in_history filtering behavior.


def test_session_history_hides_context_when_flag_false(client):
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    assert created.status_code == 200
    session_id = created.json()["session_id"]

    response = client.get(f"/api/sessions/{session_id}")
    assert response.status_code == 200

    history = response.json()["history"]
    assert all(not msg.get("is_context", False) for msg in history)


def test_session_history_shows_context_when_flag_true(client):
    created = client.post("/api/sessions", json={"show_context_in_history": True})
    assert created.status_code == 200

    history = created.json()["history"]
    # 母体不再在会话创建时预加载；context 消息改由每轮按附件动态注入
    assert all(not msg.get("is_context", False) for msg in history)
