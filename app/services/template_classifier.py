"""Template classifier service.

Classifies an uploaded Excel file into one of 7 known document templates
(D1..D7) using the ``template_classifier`` prompt. Provides a dedicated
Excel→cells extractor (no gangbiao label rewriting), a one-shot JSON LLM
call at temperature 0, and a tolerant JSON parser.
"""

import json
import re
from typing import Any

import httpx
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
