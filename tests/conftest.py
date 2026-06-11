import sys
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main


@pytest.fixture(autouse=True)
def isolate_runtime_state(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("MATERIALS_AUTOLOAD", "false")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    main.SESSIONS.clear()
    main.MATERIALS_CONTEXT_CACHE.clear()
    yield
    main.SESSIONS.clear()
    main.MATERIALS_CONTEXT_CACHE.clear()


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    # Use a fixed user ID so that session create/get/chat can all see the same
    # sessions within a single test.  Without this, get_current_user_id() would
    # generate a fresh UUID per request and every cross-request lookup would 404.
    with TestClient(main.app) as c:
        c.headers["X-User-ID"] = "test-user"
        yield c
