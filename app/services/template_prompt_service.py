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


def iter_master_entries() -> list[tuple[str | None, Path]]:
    """Return the (document_id | None, path) pairs the preload cache should warm.

    Mirrors MASTER_REGISTRY exactly so the cache key set and the registry stay
    in lockstep: any document_id the resolver can return a path for has a
    matching cache key here. Sorted for deterministic startup logging."""
    return sorted(
        MASTER_REGISTRY.items(),
        key=lambda kv: ("0" if kv[0] is None else kv[0]) or "",
    )


from app.services.template_classifier import classify_file

_EXCEL_EXTS = (".xlsx", ".xls")

_MSG_NON_EXCEL = "请上传岗位标准化模板 Excel 文件（.xlsx/.xls）"
_MSG_UNRECOGNIZED = "附件未识别为标准模板，请确认后重传"
_MSG_MULTI_TEMPLATE = "检测到多份不同模板附件，请说明你想用哪份进行辅导"
_MSG_MASTER_MISSING = "模板母版尚未配置，请联系管理员"
_MSG_READ_FAIL = "附件读取失败，请重传"


def _intercept(message: str, reason: str = "") -> Resolution:
    return Resolution(status="intercept", intercept_message=message, reason=reason)


async def resolve_master(
    attachments: list[dict],
    base_dir: Path,
    current_template_id: str | None = None,
) -> Resolution:
    """Decide which master to load for this turn based on attachments.

    attachments: items from _save_attachments (saved_path relative to base_dir
    or absolute). current_template_id: the session's last-recognized template
    (D1..D7); reused on a no-attachment turn so a text follow-up keeps the
    same template instead of falling back to _generic. Returns ok+master_path
    or intercept+message.
    """
    if not attachments:
        # Reuse the session's current template when present; otherwise generic.
        path = get_master_path(current_template_id)
        if path is not None:
            return Resolution(
                status="ok", master_path=path, document_id=current_template_id
            )
        # current_template_id was None/invalid (or its master file is missing)
        # → fall back to the generic master.
        path = get_master_path(None)
        if path is None:
            return _intercept(_MSG_MASTER_MISSING, "generic master missing")
        return Resolution(status="ok", master_path=path, document_id=None)

    document_ids: list[str] = []
    for att in attachments:
        filename = att.get("filename") or ""
        ext = Path(filename).suffix.lower()
        if ext not in _EXCEL_EXTS:
            return _intercept(_MSG_NON_EXCEL, f"non-excel: {filename}")

        saved = att.get("saved_path") or ""
        p = Path(saved)
        if not p.is_absolute():
            p = (base_dir / p)
        try:
            raw = p.read_bytes()
        except OSError as exc:
            return _intercept(_MSG_READ_FAIL, f"read fail {saved}: {exc}")

        try:
            result = await classify_file(raw, ext)
        except Exception as exc:  # noqa: BLE001 — classifier failure -> intercept
            return _intercept(_MSG_UNRECOGNIZED, f"classifier error: {exc}")

        if not result.matched or result.document_id is None:
            return _intercept(_MSG_UNRECOGNIZED, f"unmatched {filename}")
        document_ids.append(result.document_id)

    if len(set(document_ids)) > 1:
        return _intercept(_MSG_MULTI_TEMPLATE, f"ids={document_ids}")

    doc_id = document_ids[0]
    path = get_master_path(doc_id)
    if path is None:
        return _intercept(_MSG_MASTER_MISSING, f"master missing for {doc_id}")
    return Resolution(status="ok", master_path=path, document_id=doc_id)
