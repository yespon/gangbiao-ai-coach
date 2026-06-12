import uuid
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.core.config import BASE_DIR, UPLOAD_ROOT, settings
from app.extractors.document import _extract_doc_text, _extract_docx_text, _extract_pdf_text
from app.extractors.spreadsheet import _extract_xls_text, _extract_xlsx_text



def _clip_text(text: str, limit: int) -> str:
    # limit <= 0 means "no clipping" for sending full attachment text.
    if limit <= 0:
        return text
    return text[:limit]


ATTACHMENT_EXCERPT_CHARS = settings.attachment_excerpt_chars
# 0 = no clipping: embed the full extracted text into the hint so the entire
# spreadsheet / document content is baked into user_msg.content and persists
# across all subsequent conversation turns.
ATTACHMENT_HINT_CHARS = settings.attachment_hint_chars

# When True, prepend the "附件: <name> (<size> bytes)" meta line to every hint.
# Defaults to False so the raw extracted content is sent directly without noise.
ATTACHMENT_SHOW_META = settings.attachment_show_meta


def _extract_attachment_excerpt(raw_bytes: bytes, lower_name: str) -> str:
    if lower_name.endswith((".txt", ".md", ".json", ".csv")):
        return raw_bytes.decode("utf-8", errors="ignore")

    if lower_name.endswith(".docx"):
        return _extract_docx_text(raw_bytes)

    if lower_name.endswith(".doc"):
        return _extract_doc_text(raw_bytes)

    if lower_name.endswith(".pdf"):
        return _extract_pdf_text(raw_bytes)

    if lower_name.endswith(".xlsx"):
        return _extract_xlsx_text(raw_bytes)

    if lower_name.endswith(".xls"):
        return _extract_xls_text(raw_bytes)

    return ""


async def _save_attachments(
    session_id: str,
    files: list[UploadFile],
) -> tuple[list[dict[str, Any]], list[str]]:
    saved_meta: list[dict[str, Any]] = []
    attachment_hints: list[str] = []

    if not files:
        return saved_meta, attachment_hints

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    session_dir = UPLOAD_ROOT / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    for f in files:
        raw_bytes = await f.read()
        filename = f.filename or "unnamed"
        safe_name = Path(filename).name
        target = session_dir / f"{uuid.uuid4().hex}_{safe_name}"
        target.write_bytes(raw_bytes)

        lower_name = safe_name.lower()
        excerpt = _extract_attachment_excerpt(raw_bytes=raw_bytes, lower_name=lower_name)

        meta = {
            "filename": safe_name,
            "content_type": f.content_type,
            "size": len(raw_bytes),
            "saved_path": str(target.relative_to(BASE_DIR)),
            "excerpt": _clip_text(excerpt, ATTACHMENT_EXCERPT_CHARS),
        }
        saved_meta.append(meta)

        if ATTACHMENT_SHOW_META:
            hint = f"附件: {safe_name} ({len(raw_bytes)} bytes)"
            if excerpt:
                hint += f"\n可读摘要:\n{_clip_text(excerpt, ATTACHMENT_HINT_CHARS)}"
            else:
                hint += "\n未提取到可读文本（建议使用 txt/md/json/csv/doc/docx/xls/xlsx/pdf，或粘贴关键内容）"
            attachment_hints.append(hint)
        elif excerpt:
            attachment_hints.append(_clip_text(excerpt, ATTACHMENT_HINT_CHARS))
        else:
            attachment_hints.append(
                f"附件 {safe_name} 未提取到可读文本（建议使用 txt/md/json/csv/doc/docx/xls/xlsx/pdf，或粘贴关键内容）"
            )

    return saved_meta, attachment_hints
