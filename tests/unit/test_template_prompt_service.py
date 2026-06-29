import json
from pathlib import Path

import pytest

from app.services import template_prompt_service as svc


class _FakeLogger:
    def __init__(self):
        self.errors = []
    def error(self, msg, *a, **kw):
        self.errors.append(msg.format(*a, **kw) if a or kw else msg)
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass


def test_registry_maps_all_document_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    for did in ["_generic", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
        (tmp_path / f"{did}.history.json").write_text("{}", encoding="utf-8")
    svc._build_registry()
    assert svc.get_master_path(None) == tmp_path / "_generic.history.json"
    for i in range(1, 8):
        assert svc.get_master_path(f"D{i}") == tmp_path / f"D{i}.history.json"


def test_validate_registry_logs_missing_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    # only _generic present
    (tmp_path / "_generic.history.json").write_text("{}", encoding="utf-8")
    svc._build_registry()
    log = _FakeLogger()
    svc.validate_master_registry(log)  # must not raise
    assert len(log.errors) >= 7  # D1..D7 missing


def test_validate_registry_all_present_no_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    for did in ["_generic", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
        (tmp_path / f"{did}.history.json").write_text("{}", encoding="utf-8")
    svc._build_registry()
    log = _FakeLogger()
    svc.validate_master_registry(log)
    assert log.errors == []


def test_get_master_path_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    svc._build_registry()
    assert svc.get_master_path("D3") is None


import asyncio
import pytest
from app.services.template_classifier import ClassificationResult


def _att(filename, saved_path):
    return {"filename": filename, "saved_path": saved_path, "size": 10, "excerpt": ""}


@pytest.fixture
def master_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    for did in ["_generic", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
        (tmp_path / f"{did}.history.json").write_text("{}", encoding="utf-8")
    svc._build_registry()
    return tmp_path


def _patch_classify(monkeypatch, results):
    """results: list of ClassificationResult returned in order, one per file."""
    calls = {"n": 0}

    async def fake_classify(raw_bytes, ext):
        i = calls["n"]
        calls["n"] += 1
        return results[i]
    monkeypatch.setattr(svc, "classify_file", fake_classify)


def test_resolve_no_attachments_returns_generic(master_dir):
    r = asyncio.run(svc.resolve_master([], svc.MASTER_DIR.parent))
    assert r.status == "ok"
    assert r.document_id is None
    assert r.master_path == master_dir / "_generic.history.json"


def test_resolve_non_excel_intercepts(master_dir, tmp_path, monkeypatch):
    xlsx = tmp_path / "a.pdf"
    xlsx.write_bytes(b"%PDF")
    _patch_classify(monkeypatch, [])
    r = asyncio.run(svc.resolve_master([_att("a.pdf", str(xlsx))], tmp_path))
    assert r.status == "intercept"
    assert "Excel" in r.intercept_message


def test_resolve_single_excel_ok(master_dir, tmp_path, monkeypatch):
    xlsx = tmp_path / "a.xlsx"
    xlsx.write_bytes(b"PK")
    _patch_classify(monkeypatch, [ClassificationResult(matched=True, document_id="D5")])
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(xlsx))], tmp_path))
    assert r.status == "ok"
    assert r.document_id == "D5"
    assert r.master_path == master_dir / "D5.history.json"


def test_resolve_unmatched_intercepts(master_dir, tmp_path, monkeypatch):
    xlsx = tmp_path / "a.xlsx"
    xlsx.write_bytes(b"PK")
    _patch_classify(monkeypatch, [ClassificationResult(matched=False, document_id=None)])
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(xlsx))], tmp_path))
    assert r.status == "intercept"
    assert "未识别" in r.intercept_message


def test_resolve_classifier_raises_intercepts(master_dir, tmp_path, monkeypatch):
    xlsx = tmp_path / "a.xlsx"
    xlsx.write_bytes(b"PK")

    async def boom(raw, ext):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(svc, "classify_file", boom)
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(xlsx))], tmp_path))
    assert r.status == "intercept"
    assert "未识别" in r.intercept_message


def test_resolve_multi_different_templates_intercepts(master_dir, tmp_path, monkeypatch):
    f1 = tmp_path / "a.xlsx"; f1.write_bytes(b"PK")
    f2 = tmp_path / "b.xlsx"; f2.write_bytes(b"PK")
    _patch_classify(monkeypatch, [
        ClassificationResult(matched=True, document_id="D5"),
        ClassificationResult(matched=True, document_id="D7"),
    ])
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(f1)), _att("b.xlsx", str(f2))], tmp_path))
    assert r.status == "intercept"
    assert "多份" in r.intercept_message


def test_resolve_multi_same_template_ok(master_dir, tmp_path, monkeypatch):
    f1 = tmp_path / "a.xlsx"; f1.write_bytes(b"PK")
    f2 = tmp_path / "b.xlsx"; f2.write_bytes(b"PK")
    _patch_classify(monkeypatch, [
        ClassificationResult(matched=True, document_id="D4"),
        ClassificationResult(matched=True, document_id="D4"),
    ])
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(f1)), _att("b.xlsx", str(f2))], tmp_path))
    assert r.status == "ok"
    assert r.document_id == "D4"


def test_resolve_master_file_missing_intercepts(tmp_path, monkeypatch):
    # registry missing D3 file
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    (tmp_path / "_generic.history.json").write_text("{}", encoding="utf-8")
    svc._build_registry()
    xlsx = tmp_path / "a.xlsx"; xlsx.write_bytes(b"PK")
    _patch_classify(monkeypatch, [ClassificationResult(matched=True, document_id="D3")])
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(xlsx))], tmp_path))
    assert r.status == "intercept"
    assert "母版尚未配置" in r.intercept_message


def test_resolve_read_bytes_fail_intercepts(master_dir, tmp_path, monkeypatch):
    # saved_path points to nonexistent file
    _patch_classify(monkeypatch, [])
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(tmp_path / "nope.xlsx"))], tmp_path))
    assert r.status == "intercept"
    assert "读取失败" in r.intercept_message


def test_resolve_no_attachments_with_current_template_returns_existing(master_dir):
    """会话已有 current_template="D5" 时，无附件消息沿用 D5（不回退通用母版）。"""
    r = asyncio.run(svc.resolve_master([], svc.MASTER_DIR.parent, current_template_id="D5"))
    assert r.status == "ok"
    assert r.document_id == "D5"
    assert r.master_path == master_dir / "D5.history.json"


def test_resolve_no_attachments_current_template_none_returns_generic(master_dir):
    """current_template 为 None（新会话/通用态）时，无附件消息用通用母版（向后兼容）。"""
    r = asyncio.run(svc.resolve_master([], svc.MASTER_DIR.parent, current_template_id=None))
    assert r.status == "ok"
    assert r.document_id is None
    assert r.master_path == master_dir / "_generic.history.json"


def test_resolve_no_attachments_current_template_missing_falls_back_generic(tmp_path, monkeypatch):
    """current_template 指向的母版文件缺失时，无附件回退通用母版（不拦截）。"""
    # registry has _generic but NOT D3
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    (tmp_path / "_generic.history.json").write_text("{}", encoding="utf-8")
    svc._build_registry()
    r = asyncio.run(svc.resolve_master([], tmp_path, current_template_id="D3"))
    assert r.status == "ok"
    assert r.document_id is None
    assert r.master_path == tmp_path / "_generic.history.json"
