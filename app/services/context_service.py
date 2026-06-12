import json
from pathlib import Path
from typing import Any

from app.core.config import BASE_DIR, settings
from app.models.chat import ChatMessage

MATERIALS_CONTEXT_CACHE: list[ChatMessage] = []


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


def load_default_context_messages(context_file: Path, logger) -> list[ChatMessage]:
    if not context_file.exists():
        logger.warning("Default context file not found: {}", context_file)
        return []

    raw = json.loads(context_file.read_text(encoding="utf-8"))
    messages: list[ChatMessage] = []

    header = {
        "version": raw.get("version"),
        "format": raw.get("format"),
        "generated_at": raw.get("generated_at"),
        "source_file": context_file.name,
    }
    logger.info("Default context metadata loaded: {}", json.dumps(header, ensure_ascii=False))

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
