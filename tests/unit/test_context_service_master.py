import json
from pathlib import Path

from app.services import context_service as cs
from app.services.context_service import (
    MASTER_MESSAGES_CACHE,
    load_master_messages,
    preload_master_messages,
)


class _FakeLogger:
    def __init__(self):
        self.warnings = []
        self.infos = []
    def info(self, msg, *a, **kw):
        self.infos.append(msg.format(*a, **kw) if a or kw else msg)
    def warning(self, msg, *a, **kw):
        self.warnings.append(msg.format(*a, **kw) if a or kw else msg)
    def error(self, *a, **kw): pass


def _master(tmp_path, name, msgs):
    p = tmp_path / f"{name}.history.json"
    p.write_text(json.dumps({"messages": msgs}), encoding="utf-8")
    return p


def test_load_master_messages_parses_chat_history(tmp_path):
    p = _master(tmp_path, "D5", [
        {"role": "system", "content": "你是教练"},
        {"role": "user", "content": "开始"},
        {"role": "assistant", "content": "好的"},
    ])

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
    p = tmp_path / "bad.history.json"
    p.write_text("{not json", encoding="utf-8")
    msgs = load_master_messages(p, _FakeLogger())
    assert msgs == []


# --- preload (requirement 3) ---

def test_preload_caches_all_present_masters(tmp_path, monkeypatch):
    """preload_master_messages parses every present master file into the cache."""
    entries = []
    for name in ["_generic", "D1", "D5"]:
        p = _master(tmp_path, name, [{"role": "system", "content": f"{name} body"}])
        key = None if name == "_generic" else name
        entries.append((key, p))

    count = preload_master_messages(entries, _FakeLogger())

    assert count == 3
    assert set(MASTER_MESSAGES_CACHE.keys()) == {None, "D1", "D5"}
    assert MASTER_MESSAGES_CACHE["D1"][0].content == "D1 body"


def test_preload_skips_missing_and_empty(tmp_path):
    """Missing files are skipped; empty/failed parses are skipped + warned."""
    present = _master(tmp_path, "D2", [{"role": "system", "content": "x"}])
    missing = tmp_path / "D9.history.json"  # does not exist
    empty = tmp_path / "D7.history.json"
    empty.write_text(json.dumps({"messages": []}), encoding="utf-8")  # parses → []

    log = _FakeLogger()
    count = preload_master_messages(
        [("D2", present), ("D9", missing), ("D7", empty)], log
    )

    assert count == 1  # only D2 (D7 parses to [] → not cached)
    assert "D2" in MASTER_MESSAGES_CACHE
    assert "D9" not in MASTER_MESSAGES_CACHE
    assert "D7" not in MASTER_MESSAGES_CACHE
    assert any("D7" in w for w in log.warnings)  # empty parse warned


def test_preload_is_idempotent_rebuild(tmp_path, monkeypatch):
    """A second preload clears + repopulates (no stale entries from a prior set)."""
    entries_v1 = [(None, _master(tmp_path, "_generic", [{"role": "system", "content": "g"}]))]
    preload_master_messages(entries_v1, _FakeLogger())
    assert None in MASTER_MESSAGES_CACHE

    # Rebuild with a different set — old generic entry must be gone.
    entries_v2 = [("D3", _master(tmp_path, "D3", [{"role": "system", "content": "d3"}]))]
    preload_master_messages(entries_v2, _FakeLogger())

    assert "D3" in MASTER_MESSAGES_CACHE
    assert None not in MASTER_MESSAGES_CACHE  # cleared


def test_load_returns_clone_from_cache_not_shared_list(tmp_path):
    """A cache hit must return a fresh clone — callers mutate the result, so the
    cached list itself must stay untouched across calls."""
    p = _master(tmp_path, "D4", [{"role": "system", "content": "orig"}])
    preload_master_messages([("D4", p)], _FakeLogger())

    first = load_master_messages(p, _FakeLogger())
    first.append(_msg("user", "mutated"))  # caller mutates its copy
    first[0].content = "changed"

    second = load_master_messages(p, _FakeLogger())
    assert len(second) == 1                      # not polluted by caller's append
    assert second[0].content == "orig"           # not polluted by caller's edit


def test_load_cache_miss_reads_disk_and_backfills(tmp_path):
    """An uncached master is parsed from disk and then cached for next time."""
    p = _master(tmp_path, "D6", [{"role": "system", "content": "from disk"}])
    assert "D6" not in MASTER_MESSAGES_CACHE  # cold cache

    msgs = load_master_messages(p, _FakeLogger())

    assert msgs[0].content == "from disk"
    assert "D6" in MASTER_MESSAGES_CACHE  # backfilled


def test_load_generic_uses_none_key(tmp_path):
    """The generic master path maps to the None cache key."""
    p = _master(tmp_path, "_generic", [{"role": "system", "content": "generic body"}])
    preload_master_messages([(None, p)], _FakeLogger())

    msgs = load_master_messages(p, _FakeLogger())
    assert msgs[0].content == "generic body"
    assert None in MASTER_MESSAGES_CACHE


def _msg(role, content):
    from app.models.chat import ChatMessage
    return ChatMessage(role=role, content=content, is_context=True)
