import json
from pathlib import Path

from app.services.context_service import load_master_messages


class _FakeLogger:
    def info(self, *_a, **_kw): pass
    def warning(self, *_a, **_kw): pass


def test_load_master_messages_parses_chat_history(tmp_path):
    p = tmp_path / "D5.history.json"
    p.write_text(json.dumps({
        "version": 1.0,
        "format": "openai_chat_history_with_metadata",
        "messages": [
            {"role": "system", "content": "你是教练"},
            {"role": "user", "content": "开始"},
            {"role": "assistant", "content": "好的"},
        ],
    }), encoding="utf-8")

    msgs = load_master_messages(p, _FakeLogger())

    assert len(msgs) == 3
    assert all(m.is_context for m in msgs)
    assert msgs[0].role == "system"
    assert msgs[0].content == "你是教练"
    assert msgs[1].role == "user"


def test_load_master_messages_missing_file_returns_empty(tmp_path):
    msgs = load_master_messages(tmp_path / "nope.json", _FakeLogger())
    assert msgs == []


def test_load_master_messages_malformed_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    msgs = load_master_messages(p, _FakeLogger())
    assert msgs == []
