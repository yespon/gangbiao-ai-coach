"""Template classifier service.

Classifies an uploaded Excel file into one of 7 known document templates
(D1..D7) using the ``template_classifier`` prompt. Provides a dedicated
Excel→cells extractor (no gangbiao label rewriting), a one-shot JSON LLM
call at temperature 0, and a tolerant JSON parser.
"""

import json
import re
from io import BytesIO
from typing import Any

import httpx
import xlrd
from openpyxl import load_workbook
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.prompts import template_classifier as CLASSIFIER_PROMPT

_VALID_DOCUMENT_IDS = {"D1", "D2", "D3", "D4", "D5", "D6", "D7"}


class ClassificationResult(BaseModel):
    matched: bool = False
    document_id: str | None = None
    version: str | None = None
    stage: str | None = None
    confidence: float = 0.0
    matched_signals: list[str] = Field(default_factory=list)
    reason: str = ""
    error: str | None = None


def _coerce(obj: dict[str, Any]) -> ClassificationResult:
    """Normalize a parsed JSON object into a validated ClassificationResult."""
    raw_id = obj.get("document_id")
    if isinstance(raw_id, str):
        doc_id = raw_id.strip().upper()
    else:
        doc_id = None
    if doc_id not in _VALID_DOCUMENT_IDS:
        doc_id = None

    # document_id is the source of truth for the label: reconcile `matched`.
    matched = doc_id is not None

    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    signals = obj.get("matched_signals") or []
    if not isinstance(signals, list):
        signals = []
    signals = [str(s) for s in signals]

    version = obj.get("version")
    stage = obj.get("stage")
    reason = obj.get("reason")

    return ClassificationResult(
        matched=matched,
        document_id=doc_id,
        version=None if version is None else str(version),
        stage=None if stage is None else str(stage),
        confidence=confidence,
        matched_signals=signals,
        reason="" if reason is None else str(reason),
        error=None,
    )


def parse_classification(raw: str | None) -> ClassificationResult:
    """Parse the model's raw text output into a ClassificationResult.

    Tolerant of ```json fences and surrounding prose. Never raises — on
    parse failure returns a result with ``matched=False`` and an ``error``.
    """
    text = (raw or "").strip()

    # Strip a ```json ... ``` fenced block, taking everything between the fences.
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    # If there's still leading/trailing prose, grab the outermost {...} block.
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)

    try:
        obj = json.loads(text)
    except Exception:  # noqa: BLE001 — fall back to extracting the {...} block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group(0))
            except Exception as exc:  # noqa: BLE001 — any parse failure is a handled error
                return ClassificationResult(
                    matched=False,
                    document_id=None,
                    reason=f"解析失败: {exc}",
                    error=str(exc),
                )
        else:
            return ClassificationResult(
                matched=False,
                document_id=None,
                reason="解析失败: 未找到 JSON 对象",
                error="no JSON object found",
            )

    if not isinstance(obj, dict):
        return ClassificationResult(
            matched=False,
            document_id=None,
            reason="解析失败: 模型输出不是 JSON 对象",
            error="non-object JSON",
        )

    return _coerce(obj)


def _normalize_cell(value: Any) -> str:
    """Normalize a raw cell value to the string form used for classification.

    Float integers (e.g. 5.0) become "5"; everything else is str()'d with
    only leading/trailing whitespace trimmed (internal spaces preserved so
    merged-cell fingerprints survive).
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _format_cells(
    rows: int, cols: int, cells: list[tuple[int, int, str]]
) -> str:
    lines = [f"文件尺寸：{rows}行 × {cols}列", "单元格内容（[行,列] 内容）："]
    lines.extend(f"[{r},{c}] {v}" for r, c, v in cells)
    return "\n".join(lines)


def _collect_xlsx(
    raw_bytes: bytes,
) -> tuple[int, int, list[tuple[int, int, str]]]:
    """Return (rows, cols, non-empty [(row, col, value)]) for the first
    non-empty worksheet. Merged non-anchor cells read as None and are
    skipped, so only the merged top-left value appears (no fill).
    """
    wb = load_workbook(filename=BytesIO(raw_bytes), data_only=False, read_only=False)

    sheet = None
    for ws in wb.worksheets:
        if any(cell.value not in (None, "") for row in ws.iter_rows() for cell in row):
            sheet = ws
            break
    if sheet is None:
        sheet = wb.worksheets[0]

    rows = sheet.max_row or 0
    cols = sheet.max_column or 0
    cells: list[tuple[int, int, str]] = []
    for r in range(1, rows + 1):
        for c in range(1, cols + 1):
            value = sheet.cell(row=r, column=c).value
            if value in (None, ""):
                continue
            normalized = _normalize_cell(value)
            if normalized:
                cells.append((r, c, normalized))
    return rows, cols, cells


def _collect_xls(
    raw_bytes: bytes,
) -> tuple[int, int, list[tuple[int, int, str]]]:
    """Same shape as _collect_xlsx but for legacy .xls via xlrd.

    xlrd reads merged non-anchor cells as '' (empty), so only the merged
    top-left value appears naturally.
    """
    book = xlrd.open_workbook(file_contents=raw_bytes)
    sheet = book.sheets()[0]
    rows = sheet.nrows
    cols = sheet.ncols
    cells: list[tuple[int, int, str]] = []
    for r in range(rows):
        for c in range(cols):
            value = sheet.cell_value(r, c)
            if value in (None, ""):
                continue
            normalized = _normalize_cell(value)
            if normalized:
                cells.append((r + 1, c + 1, normalized))  # 1-based for output
    return rows, cols, cells


def extract_cells_for_classification(raw_bytes: bytes, ext: str) -> str:
    """Extract an Excel file into the prompt's expected input format.

    ``ext`` is the lowercased file extension including the dot, e.g. ".xlsx".
    Raises RuntimeError for unsupported extensions.
    """
    ext = (ext or "").lower()
    if ext == ".xlsx":
        rows, cols, cells = _collect_xlsx(raw_bytes)
    elif ext == ".xls":
        rows, cols, cells = _collect_xls(raw_bytes)
    else:
        raise RuntimeError(f"不支持的文件扩展名: {ext!r}（仅支持 .xlsx / .xls）")
    return _format_cells(rows, cols, cells)
