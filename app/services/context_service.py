import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.core.config import BASE_DIR, settings
from app.models.chat import ChatMessage

MATERIALS_CONTEXT_CACHE: list[ChatMessage] = []

# Preload cache (requirement 3 "preload"): {document_id | None: [ChatMessage]}.
# Populated once at startup by preload_master_messages(); load_master_messages()
# returns a clone from here and only hits disk on a miss (then backfills), so
# per-turn cost is a clone, not a file read + JSON parse.
MASTER_MESSAGES_CACHE: dict[str | None, list[ChatMessage]] = {}

_GENERIC_STEM = "_generic"
_KEY_SEPARATOR = ".history"


def _master_key_from_path(path: Path) -> str | None:
    """Derive the cache key (document_id, or None for the generic master) from a
    master file path. Convention: ``{D1..D7}.history.json`` /
    ``_generic.history.json`` — kept in sync with template_prompt_service's
    MASTER_REGISTRY so a path resolved by the registry maps back to its key.
    Strips the ``.history.json`` suffix so e.g. ``D6.history.json`` → ``"D6"``."""
    stem = path.name
    if _KEY_SEPARATOR in stem:
        stem = stem.split(_KEY_SEPARATOR, 1)[0]
    return None if stem == _GENERIC_STEM else stem


def _clone_context_messages(messages: list[ChatMessage]) -> list[ChatMessage]:
    cloned: list[ChatMessage] = []
    for msg in messages:
        cloned.append(
            ChatMessage(
                role=msg.role,
                content=msg.content,
                is_context=msg.is_context,
                visible_in_history=msg.visible_in_history,
                attachments=[dict(a) for a in msg.attachments],
            )
        )
    return cloned


def _attachments_from_history_metadata(meta: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not meta:
        return []

    raw = meta.get("attachments")
    if not isinstance(raw, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue

        filename = str(item.get("filename") or "unnamed")
        original_path = item.get("original_path")
        normalized.append(
            {
                "filename": filename,
                "content_type": item.get("content_type"),
                "size": item.get("size"),
                "saved_path": item.get("saved_path"),
                "original_path": str(original_path) if original_path else None,
                "source": "history_context",
            }
        )

    return normalized


def _compact_metadata_summary(meta: dict[str, Any] | None) -> dict[str, Any]:
    if not meta:
        return {}

    summary: dict[str, Any] = {}

    if "attachments" in meta:
        attachments = _attachments_from_history_metadata(meta)
        if attachments:
            summary["attachments"] = [
                {
                    "filename": item.get("filename"),
                    "original_path": item.get("original_path"),
                }
                for item in attachments
            ]

    if "details" in meta and isinstance(meta.get("details"), list):
        details = [
            str(d.get("summary"))
            for d in meta["details"]
            if isinstance(d, dict) and d.get("summary")
        ]
        if details:
            summary["details_summary"] = details[:6]

    for key in ("source", "tool", "stage", "note"):
        if key in meta and meta[key] not in (None, ""):
            summary[key] = meta[key]

    return summary


def _parse_master_file(master_path: Path, logger) -> list[ChatMessage]:
    """Read + parse a master JSON from disk into context messages. Returns []
    when the file is missing or unparseable (caller intercepts on empty)."""
    if not master_path.exists():
        logger.warning("Master file not found: {}", master_path)
        return []
    try:
        raw = json.loads(master_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Master file unparseable {}: {}", master_path, exc)
        return []

    header = {
        "version": raw.get("version"),
        "format": raw.get("format"),
        "generated_at": raw.get("generated_at"),
        "source_file": master_path.name,
    }
    logger.info("Master metadata loaded: {}", json.dumps(header, ensure_ascii=False))

    messages: list[ChatMessage] = []
    for item in raw.get("messages", []):
        role = item.get("role", "user")
        content = item.get("content", "")
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else None
        attachments = _attachments_from_history_metadata(meta)
        payload = content
        summary = _compact_metadata_summary(meta)
        if summary:
            payload = (
                f"{content}\n\n[metadata]\n"
                f"{json.dumps(summary, ensure_ascii=False)}"
            )
        messages.append(
            ChatMessage(
                role=role,
                content=payload,
                is_context=True,
                attachments=attachments,
            )
        )
    return messages


def load_master_messages(master_path: Path, logger) -> list[ChatMessage]:
    """Load a master prompt (OpenAI chat history JSON) as context messages.

    Serves from the preload cache (requirement 3): on a hit it returns a clone
    of the cached list (callers mutate the result — e.g. _build_model_messages
    appends it — so we never hand out the shared cache list itself). On a miss
    it parses the file from disk and backfills the cache so subsequent turns are
    cheap. Returns [] when the file is missing or unparseable, so callers can
    intercept (HTTP 400) instead of crashing on a corrupt master.
    """
    key = _master_key_from_path(master_path)
    cached = MASTER_MESSAGES_CACHE.get(key)
    if cached is not None:
        return _clone_context_messages(cached)

    messages = _parse_master_file(master_path, logger)
    # Backfill even an empty list so a persistently-missing/corrupt master
    # doesn't re-hit disk every turn — empty is a stable signal the caller
    # already intercepts on. Only keys we can derive (stems matching our
    # naming convention) are cached; exotic paths stay uncached.
    if key is not None or master_path.stem == _GENERIC_STEM:
        MASTER_MESSAGES_CACHE[key] = messages
    return messages


def preload_master_messages(
    master_paths: Iterable[tuple[str | None, Path]],
    logger,
) -> int:
    """Populate MASTER_MESSAGES_CACHE at startup.

    master_paths: iterable of (key, path) pairs — key is the document_id or
    None for the generic master, path the .history.json file. Each existing,
    parseable file is read once and cached; missing/unparseable files are
    skipped (validate_master_registry already logs them at ERROR). Returns the
    number of masters successfully cached. Safe to call again (idempotent
    rebuild — clears first).
    """
    MASTER_MESSAGES_CACHE.clear()
    count = 0
    for key, path in master_paths:
        if not path.exists():
            continue  # logged by validate_master_registry
        messages = _parse_master_file(path, logger)
        if messages:
            MASTER_MESSAGES_CACHE[key] = messages
            count += 1
        else:
            logger.warning("master_preload_skip key={} path={} (empty/unparseable)", key, path)
    logger.info("master_preload_loaded count={}", count)
    return count


def load_default_context_messages(context_file: Path, logger) -> list[ChatMessage]:
    """Backward-compatible wrapper: load the default (D5) master as context."""
    return load_master_messages(context_file, logger)


def load_materials_context_messages(
    supported_attachment_exts: tuple[str, ...],
    extract_attachment_excerpt,
    logger,
) -> list[ChatMessage]:
    global MATERIALS_CONTEXT_CACHE

    if not settings.materials_autoload:
        return []

    if MATERIALS_CONTEXT_CACHE:
        return _clone_context_messages(MATERIALS_CONTEXT_CACHE)

    materials_dir = settings.resolved_materials_dir()
    if not materials_dir:
        return []

    if not materials_dir.exists() or not materials_dir.is_dir():
        logger.warning("MATERIALS_DIR does not exist or is not a directory: {}", materials_dir)
        return []

    max_files = max(settings.materials_max_files, 1)
    max_excerpt_chars = max(settings.materials_max_excerpt_chars, 200)

    candidates = sorted(
        [
            p for p in materials_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in supported_attachment_exts
        ],
        key=lambda p: str(p),
    )[:max_files]

    if not candidates:
        logger.info("No supported materials found in MATERIALS_DIR: {}", materials_dir)
        return []

    blocks: list[str] = []
    attachments: list[dict[str, Any]] = []

    for path in candidates:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            logger.warning("Failed to read material file {}: {}", path, exc)
            continue

        excerpt = extract_attachment_excerpt(raw_bytes=raw, lower_name=path.name.lower())
        if not excerpt:
            continue

        rel = str(path.relative_to(BASE_DIR)) if path.is_relative_to(BASE_DIR) else str(path)
        attachments.append(
            {
                "filename": path.name,
                "size": len(raw),
                "saved_path": rel,
                "original_path": str(path),
                "source": "materials_dir",
            }
        )
        blocks.append(
            f"[教材] {path.name}\n"
            f"路径: {path}\n"
            f"摘要:\n{excerpt[:max_excerpt_chars]}"
        )

    if not blocks:
        logger.info("No readable excerpts extracted from MATERIALS_DIR: {}", materials_dir)
        return []

    content = (
        f"以下内容来自自动加载教材目录: {materials_dir}\n"
        f"已加载 {len(blocks)} 份材料（最多 {max_files} 份）。\n\n"
        + "\n\n---\n\n".join(blocks)
    )

    MATERIALS_CONTEXT_CACHE = [
        ChatMessage(
            role="system",
            content=content,
            is_context=True,
            visible_in_history=False,
            attachments=attachments,
        )
    ]

    logger.info("Loaded {} materials into default context from {}", len(blocks), materials_dir)
    return _clone_context_messages(MATERIALS_CONTEXT_CACHE)
