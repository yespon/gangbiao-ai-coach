# Template Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a template-classifier service on the existing `template_classifier` prompt (Excel→cells extraction + one-shot JSON LLM call + tolerant parsing), with deterministic unit tests and a real-LLM eval CLI that reports accuracy / per-class precision·recall·F1 / macro-F1 / confusion matrix.

**Architecture:** A single cohesive service module `app/services/template_classifier.py` (dedicated extractor that does NOT rewrite gangbiao labels, an async one-shot JSON LLM call at temperature 0, a tolerant JSON parser, and a pydantic result model). Deterministic unit tests mock the LLM call. A standalone CLI `scripts/eval_classifier.py` runs real cases through the full pipeline and computes metrics.

**Tech Stack:** Python 3.13, FastAPI app, openpyxl 3.1.5 (.xlsx), xlrd 2.0.2 (.xls), httpx 0.28.1 (LLM), pydantic v2 (result model), pytest 9 + pytest-asyncio (strict mode), OpenAI-compatible chat-completions API.

## Global Constraints

- Python 3.13; run tests with `.venv/bin/python -m pytest` from project root (`/root/workspace/projects/chat_with_gb`).
- pytest-asyncio is in **strict mode** (no `asyncio_mode` configured) → every async test MUST carry `@pytest.mark.asyncio`.
- LLM calls go through `settings.openai_api_key` / `settings.openai_model` / `settings.openai_base_url` (from `app.core.config`), POST to `{base_url}/chat/completions`, `Authorization: Bearer <key>`. Classifier uses `temperature: 0`.
- The extractor MUST NOT apply gangbiao label rewriting (`目的:` → `任务目的：` etc.) and MUST NOT inject `[Sheet]/[Structured]/[Raw]` markers — these corrupt the D1/D5 fingerprint (`岗位任务的目的` vs `岗位任务的目的和成果`).
- Merged cells are read as **top-left value only, no fill** (openpyxl/xlrd naturally yield `None`/`''` for non-anchor cells) — duplicated fingerprint text must not appear.
- Internal whitespace in cell values is preserved (`str(v).strip()` only trims ends) so merged-cell fingerprints like `拟认证等级              实际时间投入占比` survive.
- Missing `OPENAI_API_KEY` → service raises `RuntimeError` (no silent fallback — classification is an internal pipeline, and silent fallback would corrupt eval metrics).
- The prompt constant is imported with an alias to avoid a name collision between the module `template_classifier` and the imported string: `from app.services.prompts import template_classifier as CLASSIFIER_PROMPT`.

## File Structure

- **Create** `app/services/template_classifier.py` — the service: `ClassificationResult` model, `parse_classification`, `extract_cells_for_classification`, `_call_llm_json`, `classify_text`, `classify_file`, `predicted_label`. Built incrementally across Tasks 1–3.
- **Create** `tests/unit/test_template_classifier.py` — deterministic unit tests (parser, extractor, async classify with mocked LLM).
- **Create** `scripts/eval_classifier.py` — eval CLI: `load_cases`, `compute_metrics`, `run_one`, `_run_all`, `_print_report`, `main`.
- **Create** `tests/unit/test_classifier_eval_metrics.py` — deterministic tests for `compute_metrics` and `load_cases` (loads the CLI via importlib; no LLM).
- **Create** `tests/fixtures/classifier/cases/.gitkeep` — empty dir for the user's real cases later.
- **Create** `tests/fixtures/classifier/labels.json` — empty `{}`; user fills with `{"<filename>": "D1".."D7" | "NONE"}`.
- **Modify** `.gitignore` — add `reports/` so eval JSON reports aren't committed.

---

### Task 1: Result model + tolerant JSON parser

**Files:**
- Create: `app/services/template_classifier.py`
- Test: `tests/unit/test_template_classifier.py`

**Interfaces:**
- Consumes: `app.services.prompts.template_classifier` (the prompt string; not used yet in this task but imported so the alias is in place).
- Produces: `ClassificationResult` (pydantic model), `parse_classification(raw: str) -> ClassificationResult`, internal `_coerce(obj: dict) -> ClassificationResult`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_template_classifier.py`:

```python
import pytest

from app.services import template_classifier as svc
from app.services.template_classifier import ClassificationResult, parse_classification


def test_parse_clean_json():
    raw = (
        '{"matched": true, "document_id": "D1", "version": "多等级版", '
        '"stage": "阶段一【岗位价值和岗位任务】", "confidence": 0.97, '
        '"matched_signals": ["列头含「岗位任职资格等级」"], "reason": "命中D1"}'
    )
    r = parse_classification(raw)
    assert r.matched is True
    assert r.document_id == "D1"
    assert r.version == "多等级版"
    assert r.confidence == 0.97
    assert r.matched_signals == ["列头含「岗位任职资格等级」"]
    assert r.error is None


def test_parse_json_inside_code_fence_with_prose():
    raw = (
        "好的，分类结果如下：\n"
        "```json\n"
        '{"matched": true, "document_id": "D5", "confidence": 0.9, '
        '"matched_signals": [], "reason": "x"}\n'
        "```\n"
        "以上。"
    )
    r = parse_classification(raw)
    assert r.document_id == "D5"
    assert r.matched is True


def test_parse_json_with_trailing_prose_no_fence():
    raw = (
        '{"matched": false, "document_id": null, "confidence": 0.0, '
        '"matched_signals": [], "reason": "无匹配"} 这是一段说明文字'
    )
    r = parse_classification(raw)
    assert r.matched is False
    assert r.document_id is None
    assert r.error is None


def test_parse_malformed_returns_error_result():
    r = parse_classification("这不是JSON")
    assert r.matched is False
    assert r.document_id is None
    assert r.error is not None
    assert "解析失败" in r.reason


def test_coerce_invalid_document_id_becomes_none_and_unmatched():
    r = parse_classification('{"matched": true, "document_id": "D9", "confidence": 1.5}')
    assert r.document_id is None
    assert r.matched is False  # invalid id forces unmatched
    assert r.confidence == 1.0  # clamped to [0, 1]


def test_coerce_lowercase_document_id_uppercased():
    r = parse_classification('{"matched": true, "document_id": "d1", "confidence": -0.2}')
    assert r.document_id == "D1"
    assert r.confidence == 0.0  # clamped


def test_classification_result_defaults():
    r = ClassificationResult()
    assert r.matched is False
    assert r.document_id is None
    assert r.confidence == 0.0
    assert r.matched_signals == []
    assert r.error is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_template_classifier.py -v`
Expected: FAIL with `ImportError: cannot import name 'ClassificationResult'` (module/functions not defined yet).

- [ ] **Step 3: Write minimal implementation**

Create `app/services/template_classifier.py`:

```python
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
    except Exception as exc:  # noqa: BLE001 — any parse failure is a handled error
        return ClassificationResult(
            matched=False,
            document_id=None,
            reason=f"解析失败: {exc}",
            error=str(exc),
        )

    if not isinstance(obj, dict):
        return ClassificationResult(
            matched=False,
            document_id=None,
            reason="解析失败: 模型输出不是 JSON 对象",
            error="non-object JSON",
        )

    return _coerce(obj)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_template_classifier.py -v`
Expected: PASS — all 7 tests green.

- [ ] **Step 5: Commit**

```bash
git add app/services/template_classifier.py tests/unit/test_template_classifier.py
git commit -m "feat(classifier): add ClassificationResult model and tolerant JSON parser"
```

---

### Task 2: Dedicated Excel extractor

**Files:**
- Modify: `app/services/template_classifier.py` (append extractor functions)
- Test: `tests/unit/test_template_classifier.py` (append extractor tests)

**Interfaces:**
- Consumes: openpyxl (`load_workbook`), xlrd (`open_workbook`).
- Produces: `extract_cells_for_classification(raw_bytes: bytes, ext: str) -> str` — emits the prompt's expected input format:
  ```
  文件尺寸：{rows}行 × {cols}列
  单元格内容（[行,列] 内容）：
  [1,1] 岗位名称
  ...
  ```

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_template_classifier.py`:

```python
from io import BytesIO

from openpyxl import Workbook

from app.services.template_classifier import extract_cells_for_classification


def _save_xlsx(worksheet_setup):
    wb = Workbook()
    ws = wb.active
    ws.title = "T"
    worksheet_setup(ws)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_extract_formats_header_and_non_empty_cells():
    def setup(ws):
        ws["A1"] = "岗位名称"
        ws["B1"] = "服务客户"

    text = extract_cells_for_classification(_save_xlsx(setup), ".xlsx")
    assert text.startswith("文件尺寸：1行 × 2列")
    assert "单元格内容（[行,列] 内容）：" in text
    assert "[1,1] 岗位名称" in text
    assert "[1,2] 服务客户" in text


def test_extract_preserves_merged_top_left_only_and_internal_spaces():
    def setup(ws):
        ws["A1"] = "岗位名称"
        ws["C1"] = 5.0  # float that is an integer -> "5"
        ws.merge_cells("A2:A3")
        ws["A2"] = "核心任务"  # merged anchor; A3 must NOT appear
        ws["B2"] = "拟认证等级              实际时间投入占比"  # internal spaces preserved
        ws.merge_cells("B2:B3")

    text = extract_cells_for_classification(_save_xlsx(setup), ".xlsx")
    # 3 rows (A1/B2/A3 merged span), 3 cols (A,B,C)
    assert text.startswith("文件尺寸：3行 × 3列")
    assert "[1,1] 岗位名称" in text
    assert "[1,3] 5" in text  # float-int normalized to "5"
    assert "[2,1] 核心任务" in text
    assert "[2,2] 拟认证等级              实际时间投入占比" in text  # internal spaces kept
    assert "[3,1]" not in text  # merged non-anchor skipped
    assert "[3,2]" not in text  # merged non-anchor skipped


def test_extract_normalizes_float_integer_values():
    def setup(ws):
        ws["A1"] = 7.0
        ws["A2"] = 3.5  # genuine float stays as-is
        ws["A3"] = "文本"

    text = extract_cells_for_classification(_save_xlsx(setup), ".xlsx")
    assert "[1,1] 7" in text
    assert "[1,1] 7.0" not in text
    assert "[2,1] 3.5" in text
    assert "[3,1] 文本" in text


def test_extract_uses_first_non_empty_worksheet():
    wb = Workbook()
    wb.active.title = "空表"  # default empty sheet
    ws2 = wb.create_sheet("数据")
    ws2["A1"] = "岗位名称"
    buf = BytesIO()
    wb.save(buf)
    text = extract_cells_for_classification(buf.getvalue(), ".xlsx")
    assert "[1,1] 岗位名称" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_template_classifier.py -v -k extract`
Expected: FAIL with `ImportError: cannot import name 'extract_cells_for_classification'`.

- [ ] **Step 3: Write minimal implementation**

Append to `app/services/template_classifier.py` (add `BytesIO` to the `io` import — replace the existing `import json` / `import re` block's neighbors; add these imports near the top, after `import re`):

```python
from io import BytesIO
```

Add `load_workbook` and `xlrd` imports (after the `httpx` import):

```python
import xlrd
from openpyxl import load_workbook
```

Append the extractor functions after `parse_classification`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_template_classifier.py -v`
Expected: PASS — all parser + extractor tests green.

- [ ] **Step 5: Commit**

```bash
git add app/services/template_classifier.py tests/unit/test_template_classifier.py
git commit -m "feat(classifier): add dedicated Excel→cells extractor (no label rewriting)"
```

> **Note on .xls coverage:** `.xls` extraction (`_collect_xls`) mirrors `.xlsx` logic but is not unit-tested here because the project has no `xlwt` dependency to author a `.xls` fixture in-memory. It will be exercised end-to-end when the user supplies `.xls` eval cases. `.xlsx` is fully covered.

---

### Task 3: LLM call + classify entrypoints

**Files:**
- Modify: `app/services/template_classifier.py` (append LLM call + classify functions)
- Test: `tests/unit/test_template_classifier.py` (append async tests)

**Interfaces:**
- Consumes: `settings` (openai_api_key/model/base_url), `httpx`, `CLASSIFIER_PROMPT`, `extract_cells_for_classification`, `parse_classification`.
- Produces: `_call_llm_json(messages) -> str` (async; raises RuntimeError on missing key / non-200), `classify_text(text) -> ClassificationResult` (async), `classify_file(raw_bytes, ext) -> ClassificationResult` (async), `predicted_label(result) -> str` (returns `document_id` or `"NONE"`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_template_classifier.py`:

```python
@pytest.mark.asyncio
async def test_classify_text_sends_prompt_and_parses_result(monkeypatch):
    captured = {}

    async def fake_call(messages):
        captured["messages"] = messages
        return (
            '{"matched": true, "document_id": "D1", "version": "多等级版", '
            '"stage": "阶段一【岗位价值和岗位任务】", "confidence": 0.97, '
            '"matched_signals": ["s1"], "reason": "ok"}'
        )

    monkeypatch.setattr(svc, "_call_llm_json", fake_call)

    result = await svc.classify_text(
        "文件尺寸：1行 × 1列\n单元格内容（[行,列] 内容）：\n[1,1] 岗位名称"
    )

    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][0]["content"] == svc.CLASSIFIER_PROMPT
    assert captured["messages"][1]["role"] == "user"
    assert "岗位名称" in captured["messages"][1]["content"]
    assert result.document_id == "D1"
    assert result.matched is True


@pytest.mark.asyncio
async def test_classify_file_extracts_then_classifies(monkeypatch):
    captured = {}

    async def fake_call(messages):
        captured["user"] = messages[-1]["content"]
        return (
            '{"matched": true, "document_id": "D4", "version": "通用版", '
            '"stage": "阶段四【关键活动工作分解表】", "confidence": 0.95, '
            '"matched_signals": [], "reason": "ok"}'
        )

    monkeypatch.setattr(svc, "_call_llm_json", fake_call)

    def setup(ws):
        ws["A1"] = "关键活动名称"
        ws["B1"] = "列为关键活动的理由"

    result = await svc.classify_file(_save_xlsx(setup), ".xlsx")

    assert "文件尺寸" in captured["user"]
    assert "关键活动名称" in captured["user"]
    assert result.document_id == "D4"


@pytest.mark.asyncio
async def test_call_llm_json_raises_without_api_key(monkeypatch):
    monkeypatch.setattr(svc.settings, "openai_api_key", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        await svc._call_llm_json([{"role": "user", "content": "x"}])


def test_predicted_label_returns_document_id_or_none():
    assert svc.predicted_label(
        ClassificationResult(matched=True, document_id="D1")
    ) == "D1"
    assert svc.predicted_label(
        ClassificationResult(matched=False, document_id=None)
    ) == "NONE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_template_classifier.py -v -k "classify_text or classify_file or call_llm_json or predicted_label"`
Expected: FAIL with `AttributeError: module ... has no attribute 'classify_text'` (functions not defined yet).

- [ ] **Step 3: Write minimal implementation**

Append to `app/services/template_classifier.py` (after `extract_cells_for_classification`):

```python
async def _call_llm_json(messages: list[dict[str, str]]) -> str:
    """One-shot LLM call returning the raw message content (temperature 0).

    Raises RuntimeError if the API key is missing or the response is an error.
    """
    api_key = settings.openai_api_key.strip()
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY，无法调用模板分类器")

    model = settings.openai_model
    base_url = settings.openai_base_url.rstrip("/")
    url = f"{base_url}/chat/completions"
    payload = {"model": model, "messages": messages, "temperature": 0}
    headers = {"Authorization": f"Bearer {api_key}"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(
                f"LLM 调用失败: {response.status_code} {response.text}"
            )
        data = response.json()

    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"LLM 返回无 choices: {data}")
    content = choices[0].get("message", {}).get("content")
    return content or ""


async def classify_text(text: str) -> ClassificationResult:
    """Classify pre-extracted cell text into a template."""
    messages = [
        {"role": "system", "content": CLASSIFIER_PROMPT},
        {"role": "user", "content": text},
    ]
    raw = await _call_llm_json(messages)
    return parse_classification(raw)


async def classify_file(raw_bytes: bytes, ext: str) -> ClassificationResult:
    """Extract an Excel file and classify it."""
    text = extract_cells_for_classification(raw_bytes, ext)
    return await classify_text(text)


def predicted_label(result: ClassificationResult) -> str:
    """Map a result to an eval label: the document_id, or 'NONE' when unmatched."""
    return result.document_id if result.document_id else "NONE"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_template_classifier.py -v`
Expected: PASS — all tests (parser + extractor + async classify) green.

- [ ] **Step 5: Commit**

```bash
git add app/services/template_classifier.py tests/unit/test_template_classifier.py
git commit -m "feat(classifier): add LLM call, classify_text/classify_file, predicted_label"
```

---

### Task 4: Eval fixtures scaffold + gitignore

**Files:**
- Create: `tests/fixtures/classifier/cases/.gitkeep`
- Create: `tests/fixtures/classifier/labels.json`
- Modify: `.gitignore`

**Interfaces:**
- Produces: the directories/files the eval CLI (Task 5) reads — `tests/fixtures/classifier/cases/` (user drops `.xlsx`/`.xls` here) and `tests/fixtures/classifier/labels.json` (`{"<filename>": "D1".."D7" | "NONE"}`).

- [ ] **Step 1: Create the fixtures scaffold**

Create `tests/fixtures/classifier/cases/.gitkeep` (empty file) so the directory is tracked.

Create `tests/fixtures/classifier/labels.json`:

```json
{}
```

> The user fills this later, e.g.:
> ```json
> { "case-d1.xlsx": "D1", "case-d5.xlsx": "D5", "not-a-template.xlsx": "NONE" }
> ```

- [ ] **Step 2: Add reports/ to .gitignore**

In `.gitignore`, append after the `logs/*.log` block (the second occurrence, around line 29):

Append these lines to `.gitignore`:

```
# Eval reports (generated by scripts/eval_classifier.py)
reports/
```

- [ ] **Step 3: Verify the scaffold**

Run: `ls tests/fixtures/classifier/cases/ && cat tests/fixtures/classifier/labels.json && git check-ignore reports/`
Expected: `.gitkeep` listed; `{}` printed; `reports/` reported as ignored (git check-ignore exits 0 and prints `reports/`). If `reports/` doesn't exist yet, `git check-ignore` still confirms the pattern matches.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/classifier/.gitkeep tests/fixtures/classifier/cases/.gitkeep tests/fixtures/classifier/labels.json .gitignore
git commit -m "chore(classifier): scaffold eval fixtures dir + labels.json, gitignore reports/"
```

> Note: `tests/fixtures/classifier/.gitkeep` may also be needed if git doesn't track the parent without a file — the `labels.json` file already makes the parent trackable, so `.gitkeep` at the `classifier/` level is optional. If `git add tests/fixtures/classifier/labels.json` succeeds, the directory is tracked; no extra `.gitkeep` needed there. Adjust the `git add` to match what actually exists.

---

### Task 5: Eval CLI + metrics tests

**Files:**
- Create: `scripts/eval_classifier.py`
- Test: `tests/unit/test_classifier_eval_metrics.py`

**Interfaces:**
- Consumes: `app.core.config.settings`, `app.services.template_classifier.classify_file`, `app.services.template_classifier.predicted_label`.
- Produces: `scripts/eval_classifier.py` with `load_cases(cases_dir, labels_path) -> list[tuple[str, str, Path]]`, `compute_metrics(results: list[dict]) -> dict`, `run_one(...)`, `_run_all(cases)`, `_print_report(metrics, results)`, `main()`. The CLI is run as `python scripts/eval_classifier.py --cases <dir> --labels <json> --report <out.json>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_classifier_eval_metrics.py`:

```python
import importlib.util
import json
from pathlib import Path

import pytest


def _load_eval_module():
    """Load scripts/eval_classifier.py as a module (scripts/ is not a package)."""
    path = Path(__file__).resolve().parents[2] / "scripts" / "eval_classifier.py"
    spec = importlib.util.spec_from_file_location("eval_classifier", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_compute_metrics_perfect_predictions():
    mod = _load_eval_module()
    results = [
        {"expected": "D1", "predicted": "D1"},
        {"expected": "D5", "predicted": "D5"},
        {"expected": "NONE", "predicted": "NONE"},
    ]
    m = mod.compute_metrics(results)
    assert m["accuracy"] == 1.0
    assert m["macro_f1"] == 1.0
    assert m["per_class"]["D1"]["precision"] == 1.0
    assert m["per_class"]["D1"]["recall"] == 1.0
    assert m["per_class"]["D1"]["support"] == 1
    assert m["errors"] == 0


def test_compute_metrics_misclassification_and_error_bucket():
    mod = _load_eval_module()
    results = [
        {"expected": "D1", "predicted": "D5"},    # D1 FN, D5 FP
        {"expected": "D5", "predicted": "D5"},    # D5 TP
        {"expected": "D3", "predicted": "ERROR"},  # errored -> D3 FN, no FP, error bucket
    ]
    m = mod.compute_metrics(results)
    # accuracy: 1 of 3 correct
    assert m["accuracy"] == pytest.approx(1 / 3)
    # D1: never predicted -> precision 0 (denominator 0), recall 0
    assert m["per_class"]["D1"]["precision"] == 0.0
    assert m["per_class"]["D1"]["recall"] == 0.0
    # D5: tp=1, fp=1, fn=0 -> precision 0.5, recall 1.0
    assert m["per_class"]["D5"]["precision"] == 0.5
    assert m["per_class"]["D5"]["recall"] == 1.0
    # D3: errored counts as FN -> recall 0
    assert m["per_class"]["D3"]["recall"] == 0.0
    # confusion matrix rows=actual, cols=predicted
    assert m["confusion_matrix"]["D1"]["D5"] == 1
    assert m["confusion_matrix"]["D3"]["ERROR"] == 1
    # error bucket
    assert m["errors"] == 1


def test_compute_metrics_empty_results():
    mod = _load_eval_module()
    m = mod.compute_metrics([])
    assert m["accuracy"] == 0.0
    assert m["macro_f1"] == 0.0
    assert m["errors"] == 0
    assert m["total"] == 0


def test_load_cases_skips_missing_and_uppercases_labels(tmp_path):
    mod = _load_eval_module()
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "a.xlsx").write_bytes(b"fake")
    (cases_dir / "b.xlsx").write_bytes(b"fake")  # not in labels -> skipped
    labels = {"a.xlsx": "d1", "missing.xlsx": "D2"}  # missing file + lowercase label
    (tmp_path / "labels.json").write_text(json.dumps(labels), encoding="utf-8")

    cases = mod.load_cases(str(cases_dir), str(tmp_path / "labels.json"))

    assert [c[0] for c in cases] == ["a.xlsx"]  # missing skipped, b not labeled
    assert cases[0][1] == "D1"  # label uppercased
    assert cases[0][2] == cases_dir / "a.xlsx"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_classifier_eval_metrics.py -v`
Expected: FAIL with `FileNotFoundError` or `ModuleNotFoundError` (the script does not exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `scripts/eval_classifier.py`:

```python
#!/usr/bin/env python3
"""Evaluate the template classifier against labeled Excel cases.

Loads labeled .xlsx/.xls cases, runs each through the full classifier
pipeline (extract -> classify via real LLM), and reports accuracy,
per-class precision/recall/F1, macro-F1, and a confusion matrix.

Usage:
    python scripts/eval_classifier.py \
        --cases tests/fixtures/classifier/cases \
        --labels tests/fixtures/classifier/labels.json \
        --report reports/classifier_eval.json

labels.json format: {"<filename>": "D1".."D7" | "NONE"}
"""

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Bootstrap project root onto sys.path so `app.*` imports work when run as a script.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core.config import settings  # noqa: E402
from app.services.template_classifier import (  # noqa: E402
    classify_file,
    predicted_label,
)

LABELS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "NONE"]
_LABELS_SET = set(LABELS)


def load_cases(cases_dir: str, labels_path: str) -> list[tuple[str, str, Path]]:
    """Return [(filename, expected_label_uppercased, path)] for labeled cases
    that exist on disk. Missing files are skipped with a warning.
    """
    labels = json.loads(Path(labels_path).read_text(encoding="utf-8"))
    cases: list[tuple[str, str, Path]] = []
    for fname, expected in labels.items():
        path = Path(cases_dir) / fname
        if not path.exists():
            print(
                f"WARN: labels 中有 {fname} 但 cases 目录未找到，跳过",
                file=sys.stderr,
            )
            continue
        cases.append((fname, str(expected).strip().upper(), path))
    return cases


async def run_one(fname: str, expected: str, path: Path) -> dict:
    """Classify one case file. Never raises — errors become a predicted 'ERROR'."""
    ext = path.suffix.lower()
    raw = path.read_bytes()
    try:
        result = await classify_file(raw, ext)
        return {
            "file": fname,
            "expected": expected,
            "predicted": predicted_label(result),
            "matched": result.matched,
            "document_id": result.document_id,
            "confidence": result.confidence,
            "reason": result.reason,
            "error": result.error,
            "errored": False,
        }
    except Exception as exc:  # noqa: BLE001 — eval must keep going case-by-case
        return {
            "file": fname,
            "expected": expected,
            "predicted": "ERROR",
            "matched": False,
            "document_id": None,
            "confidence": 0.0,
            "reason": str(exc),
            "error": str(exc),
            "errored": True,
        }


async def _run_all(cases: list[tuple[str, str, Path]]) -> list[dict]:
    results: list[dict] = []
    for fname, expected, path in cases:
        r = await run_one(fname, expected, path)
        ok = r["predicted"] == r["expected"]
        marker = "✓" if ok else "✗"
        suffix = f" [ERR: {r['error']}]" if r["errored"] else ""
        print(
            f"  {marker} {fname}: expected={r['expected']} "
            f"predicted={r['predicted']}{suffix}"
        )
        results.append(r)
    return results


def compute_metrics(results: list[dict]) -> dict:
    """Compute accuracy, per-class P/R/F1, macro-F1, confusion matrix, error count.

    Errored cases (predicted 'ERROR') count as a wrong prediction: they add a
    false negative to the true class and never add a false positive (ERROR is
    not a real class). They are also reported in a separate `errors` bucket.
    """
    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()
    support: Counter = Counter()
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    errors = 0

    for r in results:
        exp = r["expected"]
        pred = r["predicted"]
        support[exp] += 1
        confusion[exp][pred] += 1
        if r.get("errored") or pred == "ERROR":
            errors += 1
        if pred == exp:
            tp[exp] += 1
        else:
            fn[exp] += 1
            if pred in _LABELS_SET:
                fp[pred] += 1

    per_class: dict[str, dict] = {}
    for label in LABELS:
        p_denom = tp[label] + fp[label]
        r_denom = tp[label] + fn[label]
        precision = tp[label] / p_denom if p_denom else 0.0
        recall = tp[label] / r_denom if r_denom else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support[label],
        }

    total = len(results)
    correct = sum(1 for r in results if r["predicted"] == r["expected"])
    accuracy = correct / total if total else 0.0
    # Macro-F1 averages over classes that actually appear (support > 0),
    # matching sklearn's default behavior for the perfect-prediction case.
    supported = [c for c in per_class.values() if c["support"] > 0]
    macro_f1 = sum(c["f1"] for c in supported) / len(supported) if supported else 0.0

    return {
        "total": total,
        "correct": correct,
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "errors": errors,
        "per_class": per_class,
        "confusion_matrix": {a: dict(b) for a, b in confusion.items()},
    }


def _print_report(metrics: dict, results: list[dict]) -> None:
    print("\n===== 评估结果 =====")
    print(
        f"总数: {metrics['total']}  正确: {metrics['correct']}  "
        f"错误(LLM): {metrics['errors']}"
    )
    print(
        f"Accuracy: {metrics['accuracy']:.4f}   Macro-F1: {metrics['macro_f1']:.4f}\n"
    )
    print(f"{'类别':<6}{'precision':>11}{'recall':>9}{'f1':>9}{'support':>9}")
    for label in LABELS:
        c = metrics["per_class"][label]
        print(
            f"{label:<6}{c['precision']:>11.4f}{c['recall']:>9.4f}"
            f"{c['f1']:>9.4f}{c['support']:>9}"
        )

    print("\n混淆矩阵 (行=实际, 列=预测):")
    cols = LABELS + (["ERROR"] if metrics["errors"] else [])
    print("实际\\预测  " + "  ".join(f"{c:>6}" for c in cols))
    for actual in LABELS:
        row = metrics["confusion_matrix"].get(actual, {})
        cells = "  ".join(f"{row.get(c, 0):>6}" for c in cols)
        print(f"{actual:>8}  {cells}")


def main() -> None:
    parser = argparse.ArgumentParser(description="评估模板分类器")
    parser.add_argument(
        "--cases", default="tests/fixtures/classifier/cases", help="案例目录"
    )
    parser.add_argument(
        "--labels", default="tests/fixtures/classifier/labels.json", help="标注文件"
    )
    parser.add_argument(
        "--report", default="reports/classifier_eval.json", help="JSON 报告输出路径"
    )
    args = parser.parse_args()

    if not settings.openai_api_key.strip():
        print(
            "ERROR: OPENAI_API_KEY 未配置，无法运行评估。请在 .env 中设置后重试。",
            file=sys.stderr,
        )
        sys.exit(2)

    cases = load_cases(args.cases, args.labels)
    if not cases:
        print(
            "没有可评估的案例（labels.json 为空或文件均不存在）。",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"评估 {len(cases)} 个案例（模型: {settings.openai_model}）...\n")
    results = asyncio.run(_run_all(cases))
    metrics = compute_metrics(results)
    _print_report(metrics, results)

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps({"metrics": metrics, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n完整报告已写入: {args.report}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_classifier_eval_metrics.py -v`
Expected: PASS — all 5 metrics/load_cases tests green.

- [ ] **Step 5: Run the full test suite to confirm nothing regressed**

Run: `.venv/bin/python -m pytest tests/unit/test_template_classifier.py tests/unit/test_classifier_eval_metrics.py -v`
Expected: PASS — all classifier + eval tests green.

- [ ] **Step 6: Smoke-test the CLI's no-cases path (no API key needed)**

Run: `.venv/bin/python scripts/eval_classifier.py --cases tests/fixtures/classifier/cases --labels tests/fixtures/classifier/labels.json`
Expected: exits with code 1 and prints `没有可评估的案例（labels.json 为空或文件均不存在）。` (because `labels.json` is `{}`).

- [ ] **Step 7: Commit**

```bash
git add scripts/eval_classifier.py tests/unit/test_classifier_eval_metrics.py
git commit -m "feat(classifier): add eval CLI with metrics (accuracy/P/R/F1/macro/confusion)"
```

---

## Self-Review

**1. Spec coverage** — checked against `docs/superpowers/specs/2026-06-26-template-classifier-design.md`:
- §4.1 `ClassificationResult` model → Task 1. ✓
- §4.2 dedicated extractor (format, first non-empty sheet, no label rewriting, merged top-left only, internal spaces, float-int) → Task 2. ✓
- §4.3 `_call_llm_json` (temperature 0, RuntimeError on missing key / non-200) → Task 3. ✓
- §4.4 `classify_text` / `classify_file` → Task 3. ✓
- §4.5 tolerant JSON parse (fence/prose/malformed) → Task 1. ✓
- §4.6 API key missing → RuntimeError → Task 3 (`test_call_llm_json_raises_without_api_key`) + Task 5 (CLI pre-check). ✓
- §5 deterministic unit tests → Tasks 1, 2, 3. ✓
- §6 eval CLI (labels.json, real LLM, per-case capture, accuracy/per-class P/R/F1/macro-F1/confusion, ERROR bucket, JSON report) → Task 5. ✓
- §7 error handling table → covered: missing key (Task 3 + Task 5), non-200 (Task 3 RuntimeError → Task 5 ERROR), unparseable JSON (Task 1 → NONE), unsupported ext (Task 2 RuntimeError → Task 5 ERROR). ✓
- §3 module layout → all files present. ✓

**2. Placeholder scan** — no TBD/TODO/"implement later"; every code step has complete code; test code is concrete. ✓

**3. Type consistency** — `ClassificationResult` fields used identically across tasks; `parse_classification`, `extract_cells_for_classification`, `classify_text`, `classify_file`, `_call_llm_json`, `predicted_label`, `compute_metrics`, `load_cases` signatures match between producer and consumer tasks. `CLASSIFIER_PROMPT` alias used consistently (avoids the module/string name collision). ✓

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-26-template-classifier.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
