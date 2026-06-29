"""Template master prompt resolution.

Maps a classifier document_id (D1..D7) to the master prompt file under
MASTER_DIR, and validates the registry at startup. resolve_master() (Task 4)
orchestrates classification + conflict/intercept logic on top of this.
"""
from dataclasses import dataclass
from pathlib import Path

from app.core.config import MASTER_DIR

_DOCUMENT_IDS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7"]
_GENERIC_KEY = None

# {document_id | None: Path}; None -> generic master. Built by _build_registry().
MASTER_REGISTRY: dict[str | None, Path] = {}


@dataclass
class Resolution:
    status: str  # "ok" | "intercept"
    master_path: Path | None = None
    document_id: str | None = None
    intercept_message: str = ""
    reason: str = ""


def _build_registry() -> None:
    """Rebuild MASTER_REGISTRY from MASTER_DIR. Call after monkeypatching MASTER_DIR in tests."""
    MASTER_REGISTRY.clear()
    MASTER_REGISTRY[_GENERIC_KEY] = MASTER_DIR / "_generic.history.json"
    for did in _DOCUMENT_IDS:
        MASTER_REGISTRY[did] = MASTER_DIR / f"{did}.history.json"


_build_registry()


def get_master_path(document_id: str | None) -> Path | None:
    """Return the configured master path for a document_id, or None if the
    file does not exist on disk. None document_id -> generic master."""
    path = MASTER_REGISTRY.get(document_id)
    if path is None or not path.exists():
        return None
    return path


def validate_master_registry(logger) -> None:
    """Log ERROR for each missing master file. Never raises — allows incremental
    rollout. Called at app startup."""
    for key, path in MASTER_REGISTRY.items():
        label = "_generic" if key is None else key
        if not path.exists():
            logger.error("master_prompt_missing label={} path={}", label, path)
