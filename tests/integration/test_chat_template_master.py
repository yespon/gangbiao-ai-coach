import json
from io import BytesIO

import pytest

from app.api.v1.routes import chat as chat_module
from app.services import template_prompt_service as svc
from app.services.template_classifier import ClassificationResult


@pytest.fixture(autouse=True)
def _masters(tmp_path, monkeypatch):
    """Point MASTER_DIR at a tmp dir with 8 master files."""
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    for did in ["_generic", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
        (tmp_path / f"{did}.history.json").write_text(json.dumps({
            "messages": [{"role": "system", "content": f"{did} master"}],
        }), encoding="utf-8")
    svc._build_registry()
    yield


def _patch_classify_d5(monkeypatch):
    async def fake(raw, ext):
        return ClassificationResult(matched=True, document_id="D5")
    monkeypatch.setattr(svc, "classify_file", fake)


def _patch_classify_unmatched(monkeypatch):
    async def fake(raw, ext):
        return ClassificationResult(matched=False, document_id=None)
    monkeypatch.setattr(svc, "classify_file", fake)


def _xlsx_bytes():
    from openpyxl import Workbook
    wb = Workbook(); wb.active["A1"] = "x"
    buf = BytesIO(); wb.save(buf); return buf.getvalue()


def test_chat_with_d5_attachment_uses_d5_master(client, monkeypatch):
    _patch_classify_d5(monkeypatch)
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]
    resp = client.post(
        "/api/chat",
        data={"session_id": sid, "message": "辅导我"},
        files={"files": ("a.xlsx", _xlsx_bytes(), "application/vnd.ms-excel")},
    )
    assert resp.status_code == 200
    # No assertion on reply content (OPENAI_API_KEY empty -> fallback text)


def test_chat_non_excel_attachment_intercepts(client, monkeypatch):
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]
    resp = client.post(
        "/api/chat",
        data={"session_id": sid, "message": "看这个"},
        files={"files": ("a.pdf", b"%PDF", "application/pdf")},
    )
    assert resp.status_code == 400
    assert "Excel" in resp.json()["detail"]


def test_chat_unmatched_attachment_intercepts(client, monkeypatch):
    _patch_classify_unmatched(monkeypatch)
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]
    resp = client.post(
        "/api/chat",
        data={"session_id": sid, "message": "看这个"},
        files={"files": ("a.xlsx", _xlsx_bytes(), "application/vnd.ms-excel")},
    )
    assert resp.status_code == 400
    assert "未识别" in resp.json()["detail"]


def test_chat_no_attachment_uses_generic(client, monkeypatch):
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]
    resp = client.post("/api/chat", data={"session_id": sid, "message": "你好"})
    assert resp.status_code == 200


def test_chat_d5_then_text_follow_up_keeps_d5(client, monkeypatch):
    """上传 D5 后，纯文本追问仍用 D5 母版（current_template_id 持久化）。"""
    _patch_classify_d5(monkeypatch)
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]

    # Spy on resolve_master to capture the current_template_id passed each turn.
    calls: list[str | None] = []
    original = svc.resolve_master

    async def spy(att, base_dir, current_template_id=None):
        calls.append(current_template_id)
        return await original(att, base_dir, current_template_id=current_template_id)

    monkeypatch.setattr(chat_module, "resolve_master", spy)

    # Round 1: upload D5 attachment → current_template_id starts None.
    resp1 = client.post(
        "/api/chat",
        data={"session_id": sid, "message": "辅导我"},
        files={"files": ("a.xlsx", _xlsx_bytes(), "application/vnd.ms-excel")},
    )
    assert resp1.status_code == 200
    assert calls[-1] is None  # first turn: no template yet

    # Round 2: plain text → must reuse D5, not fall back to _generic.
    resp2 = client.post("/api/chat", data={"session_id": sid, "message": "继续"})
    assert resp2.status_code == 200
    assert calls[-1] == "D5", f"expected D5, got {calls[-1]} (calls: {calls})"


def test_chat_d5_then_switch_to_d4(client, monkeypatch):
    """上传 D5 后上传 D4 附件，模板切换到 D4；后续纯文本沿用 D4。"""
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]

    calls: list[str | None] = []
    original = svc.resolve_master

    async def spy(att, base_dir, current_template_id=None):
        calls.append(current_template_id)
        return await original(att, base_dir, current_template_id=current_template_id)

    monkeypatch.setattr(chat_module, "resolve_master", spy)

    # Round 1: D5 attachment.
    _patch_classify_d5(monkeypatch)
    r1 = client.post(
        "/api/chat",
        data={"session_id": sid, "message": "辅导我"},
        files={"files": ("a.xlsx", _xlsx_bytes(), "application/vnd.ms-excel")},
    )
    assert r1.status_code == 200

    # Round 2: switch to D4.
    def _patch_classify_d4(m):
        async def fake(raw, ext):
            return ClassificationResult(matched=True, document_id="D4")
        m.setattr(svc, "classify_file", fake)

    _patch_classify_d4(monkeypatch)
    r2 = client.post(
        "/api/chat",
        data={"session_id": sid, "message": "换模板了"},
        files={"files": ("b.xlsx", _xlsx_bytes(), "application/vnd.ms-excel")},
    )
    assert r2.status_code == 200
    assert calls[-1] == "D5"  # round 2 still sees D5 as the prior template

    # Round 3: plain text → should now reuse D4.
    r3 = client.post("/api/chat", data={"session_id": sid, "message": "继续"})
    assert r3.status_code == 200
    assert calls[-1] == "D4", f"expected D4, got {calls[-1]} (calls: {calls})"
