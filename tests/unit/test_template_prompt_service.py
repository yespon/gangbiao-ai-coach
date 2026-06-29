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
