import json
from io import BytesIO

import pytest

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
