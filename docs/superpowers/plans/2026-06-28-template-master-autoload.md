# 模板母版自动加载 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用户上传 Excel 附件时自动识别模板类型（D1..D7），按类型每轮动态加载对应的母版提示词（OpenAI chat history 格式），拦截非标准/多模板/非 Excel 附件。

**Architecture:** 新增 `template_prompt_service`（母版注册表 + `resolve_master` 编排）；`context_service` 增 `load_master_messages`（路径参数化）；`llm_service._build_model_messages` 增 `master_messages` 参数（替代硬编码 system 行）；`chat.py` 两个路由在用户消息入库后、主 LLM 调用前插入"解析→拦截→加载母版"；会话创建不再预加载母体。

**Tech Stack:** FastAPI、openpyxl/xlrd、httpx（分类器已用）、pytest + monkeypatch。

**参考 spec：** `docs/superpowers/specs/2026-06-28-template-master-autoload-design.md`

## Global Constraints

- 仅支持 Excel 附件（.xlsx/.xls）；非 Excel 视为非标准模板，拦截。
- 母版格式 = OpenAI chat history JSON（含 system/user/assistant），与 `岗位标准化母体.history.json` 同构；加载方式同现有 `load_default_context_messages`。
- 现有母体 = D5 母版；8 份母版（`_generic` + D1..D7）存于 `master/` 目录。
- 母版内部已含 system 提示词；`_build_model_messages` 不再硬编码 `"你是岗位标准化 AI 教练…"`。
- 消息级切换：每条带附件消息重新分类、重新加载母版，不缓存分类结果。
- 拦截统一 HTTP 400 + 中文提示语；拦截在主 LLM 之前；拦截时用户消息已入库（可接受）。
- 启动校验缺母版只记 ERROR 不阻断启动。
- 测试不实调 LLM：`classify_file` 用 monkeypatch mock。

---

## File Structure

**Create:**
- `master/_generic.history.json`, `master/D1.history.json` … `master/D7.history.json` — 8 份母版（D5 内容=现有母体；其余先用最小占位，待用户后续提供真实文本）
- `app/services/template_prompt_service.py` — 母版注册表 + `Resolution` + `resolve_master` + `validate_master_registry`
- `tests/unit/test_template_prompt_service.py` — `resolve_master` 单测
- `tests/integration/test_chat_template_master.py` — 路由集成测试

**Modify:**
- `app/core/config.py` — 增 `MASTER_DIR`
- `app/services/context_service.py` — 增 `load_master_messages`，`load_default_context_messages` 改为调用它
- `app/services/llm_service.py` — `_build_model_messages` 增 `master_messages` 参数
- `app/api/v1/routes/chat.py` — 两个路由插入解析/拦截/加载
- `app/api/v1/routes/sessions.py` — 会话创建去掉预加载母体
- `app/services/session_service.py` — `rebuild_memory_session` 不变（DB 里已无母体 context 消息）
- `tests/unit/test_llm_service.py` — 现有两测试补充 `master_messages=None` 兼容断言
- `tests/integration/test_session_history_visibility.py` — 调整：会话创建不再注入 context 消息
- `tests/integration/test_persistence.py` — 调整 `test_context_messages_persisted`：母体不再在创建时持久化

---

## Task 1: 母版目录与占位文件

**Files:**
- Create: `master/_generic.history.json`, `master/D1.history.json`, `master/D2.history.json`, `master/D3.history.json`, `master/D4.history.json`, `master/D5.history.json`, `master/D6.history.json`, `master/D7.history.json`
- Move: `岗位标准化母体.history.json` → `master/D5.history.json`（内容原样）

**Interfaces:**
- Produces: `master/` 下 8 份 chat history JSON 文件，供后续任务加载。

- [ ] **Step 1: 建目录，迁移 D5，创建 7 份占位母版**

```bash
mkdir -p master
git mv "岗位标准化母体.history.json" master/D5.history.json
```

为 `_generic` 与 D1、D2、D3、D4、D6、D7 各创建最小 chat history 占位（结构同 D5，仅含一条 system + 一条 user + 一条 assistant，内容为占位说明，待用户后续替换为真实母版文本）。

占位文件内容模板（以 `master/D1.history.json` 为例，其余类推，`document_id` 与 `stage` 字段相应替换）：

```json
{
  "version": 1.0,
  "format": "openai_chat_history_with_metadata",
  "generated_at": "2026-06-28T00:00:00Z",
  "source_file": "D1",
  "messages": [
    {
      "role": "system",
      "content": "# 角色：你是岗位标准化通关 AI 教练（D1 多等级版·阶段一【岗位价值和岗位任务】）。\n\n[占位母版] 真实母版文本待提供。"
    },
    {
      "role": "user",
      "content": "开始辅导"
    },
    {
      "role": "assistant",
      "content": "[占位] 我是 D1 多等级版阶段一教练。"
    }
  ]
}
```

`master/_generic.history.json` 的 system content 改为"通用母版（无附件/兜底），真实文本待提供"。

- [ ] **Step 2: 验证 8 份文件存在且为合法 JSON**

Run:
```bash
ls master/
.venv/bin/python -c "import json,glob; [json.load(open(f,encoding='utf-8')) for f in glob.glob('master/*.json')]; print('all valid')"
```
Expected: 列出 8 个文件；打印 `all valid`。

- [ ] **Step 3: 更新 `app/core/config.py` 增加 `MASTER_DIR`**

在 `CONTEXT_FILE = BASE_DIR / "岗位标准化母体.history.json"` 这一行下方添加：

```python
MASTER_DIR = BASE_DIR / "master"
```

`CONTEXT_FILE` 保留（`sessions.py` 仍引用其 `.name` 作 `context_file` 字段，逐步迁移在后续任务）。

- [ ] **Step 4: 提交**

```bash
git add master/ app/core/config.py
git commit -m "feat(master): scaffold master/ dir with 8 template master files; add MASTER_DIR"
```

---

## Task 2: `load_master_messages` 路径参数化

**Files:**
- Modify: `app/services/context_service.py`
- Test: `tests/unit/test_context_service_master.py`

**Interfaces:**
- Consumes: 现有 `load_default_context_messages(context_file, logger)` 的解析逻辑（`context_service.py:88-125`）。
- Produces: `load_master_messages(master_path: Path, logger) -> list[ChatMessage]`，与原函数行为一致，只是路径参数化。`load_default_context_messages` 改为薄包装。

- [ ] **Step 1: 写失败测试 `tests/unit/test_context_service_master.py`**

```python
import json
from pathlib import Path

from app.services.context_service import load_master_messages


class _FakeLogger:
    def info(self, *_a, **_kw): pass
    def warning(self, *_a, **_kw): pass


def test_load_master_messages_parses_chat_history(tmp_path):
    p = tmp_path / "D5.history.json"
    p.write_text(json.dumps({
        "version": 1.0,
        "format": "openai_chat_history_with_metadata",
        "messages": [
            {"role": "system", "content": "你是教练"},
            {"role": "user", "content": "开始"},
            {"role": "assistant", "content": "好的"},
        ],
    }), encoding="utf-8")

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
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    msgs = load_master_messages(p, _FakeLogger())
    assert msgs == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_context_service_master.py -q`
Expected: FAIL — `ImportError: cannot import name 'load_master_messages'`。

- [ ] **Step 3: 实现最小代码 — 重构 `context_service.py`**

将现有 `load_default_context_messages`（`context_service.py:88-125`）重命名为 `load_master_messages(master_path: Path, logger)`，函数体里所有 `context_file` 改为 `master_path`。然后在下方加薄包装：

```python
def load_default_context_messages(context_file: Path, logger) -> list[ChatMessage]:
    """Backward-compatible wrapper: load the default (D5) master as context."""
    return load_master_messages(context_file, logger)
```

保留 import 不变。

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_context_service_master.py -q`
Expected: PASS（3 项）。

- [ ] **Step 5: 回归现有 context 相关测试**

Run: `.venv/bin/python -m pytest tests/integration/test_session_history_visibility.py tests/integration/test_persistence.py -q`
Expected: `test_session_history_visibility` 中"flag true 时含 context"会因 Task 5 移除预加载而暂时失败——本步骤先记录，Task 5 修复。其余通过。

- [ ] **Step 6: 提交**

```bash
git add app/services/context_service.py tests/unit/test_context_service_master.py
git commit -m "refactor(context): parametrize load_master_messages(path); keep load_default wrapper"
```

---

## Task 3: `template_prompt_service` — 注册表与启动校验

**Files:**
- Create: `app/services/template_prompt_service.py`（本任务只做注册表 + `Resolution` + 校验，`resolve_master` 留 Task 4）
- Test: `tests/unit/test_template_prompt_service.py`

**Interfaces:**
- Consumes: `app.core.config.MASTER_DIR`、`app.models.chat`（无）。
- Produces:
  - `Resolution`（dataclass）：`status: str`（`"ok"`|`"intercept"`）、`master_path: Path | None`、`document_id: str | None`、`intercept_message: str`、`reason: str`
  - `MASTER_REGISTRY: dict[str | None, Path]`：`{None: master/_generic, "D1": master/D1, ...}`
  - `get_master_path(document_id: str | None) -> Path | None`
  - `validate_master_registry(logger) -> None`

- [ ] **Step 1: 写失败测试 — 注册表与校验**

`tests/unit/test_template_prompt_service.py`：

```python
import json
from pathlib import Path

import pytest

from app.services import template_prompt_service as svc


class _FakeLogger:
    def __init__(self):
        self.errors = []
    def error(self, msg, *a, **kw):
        self.errors.append(msg.format(*a, **kw) if a or kw else msg)
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass


def test_registry_maps_all_document_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    for did in ["_generic", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
        (tmp_path / f"{did}.history.json").write_text("{}", encoding="utf-8")
    svc._build_registry()
    assert svc.get_master_path(None) == tmp_path / "_generic.history.json"
    for i in range(1, 8):
        assert svc.get_master_path(f"D{i}") == tmp_path / f"D{i}.history.json"


def test_validate_registry_logs_missing_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    # only _generic present
    (tmp_path / "_generic.history.json").write_text("{}", encoding="utf-8")
    svc._build_registry()
    log = _FakeLogger()
    svc.validate_master_registry(log)  # must not raise
    assert len(log.errors) >= 7  # D1..D7 missing


def test_validate_registry_all_present_no_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    for did in ["_generic", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
        (tmp_path / f"{did}.history.json").write_text("{}", encoding="utf-8")
    svc._build_registry()
    log = _FakeLogger()
    svc.validate_master_registry(log)
    assert log.errors == []


def test_get_master_path_missing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    svc._build_registry()
    assert svc.get_master_path("D3") is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_template_prompt_service.py -q`
Expected: FAIL — 模块/符号不存在。

- [ ] **Step 3: 实现注册表与校验**

`app/services/template_prompt_service.py`：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_template_prompt_service.py -q`
Expected: PASS（4 项）。

- [ ] **Step 5: 提交**

```bash
git add app/services/template_prompt_service.py tests/unit/test_template_prompt_service.py
git commit -m "feat(master): add master registry + Resolution + startup validation"
```

---

## Task 4: `resolve_master` 编排

**Files:**
- Modify: `app/services/template_prompt_service.py`（追加 `resolve_master`）
- Test: `tests/unit/test_template_prompt_service.py`（追加用例）

**Interfaces:**
- Consumes:
  - `app.services.template_classifier.classify_file(raw_bytes: bytes, ext: str) -> ClassificationResult`（已存在，返回 `document_id: str|None`、`matched: bool`、`error: str|None`）
  - `app.core.config.BASE_DIR`（用于把 `saved_path` 相对路径解析回绝对路径）
- Produces: `async resolve_master(attachments: list[dict], base_dir: Path) -> Resolution`
  - `attachments` 元素结构 = `_save_attachments` 返回的 `saved_files` 项：`{"filename","content_type","size","saved_path","excerpt"}`，`saved_path` 为相对 `BASE_DIR` 的字符串。
- 拦截提示语（与 spec §4.3 一致）：
  - 非 Excel：`"请上传岗位标准化模板 Excel 文件（.xlsx/.xls）"`
  - 识别失败/非标准：`"附件未识别为标准模板，请确认后重传"`
  - 多模板：`"检测到多份不同模板附件，请说明你想用哪份进行辅导"`
  - 母版缺失：`"模板母版尚未配置，请联系管理员"`
  - 读字节失败：`"附件读取失败，请重传"`

- [ ] **Step 1: 写失败测试 — `resolve_master` 各分支**

追加到 `tests/unit/test_template_prompt_service.py`：

```python
import asyncio
import pytest
from app.services import template_prompt_service as svc
from app.services.template_classifier import ClassificationResult


def _att(filename, saved_path):
    return {"filename": filename, "saved_path": saved_path, "size": 10, "excerpt": ""}


@pytest.fixture
def master_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    for did in ["_generic", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
        (tmp_path / f"{did}.history.json").write_text("{}", encoding="utf-8")
    svc._build_registry()
    return tmp_path


def _patch_classify(monkeypatch, results):
    """results: list of ClassificationResult returned in order, one per file."""
    calls = {"n": 0}

    async def fake_classify(raw_bytes, ext):
        i = calls["n"]
        calls["n"] += 1
        return results[i]
    monkeypatch.setattr(svc, "classify_file", fake_classify)


def test_resolve_no_attachments_returns_generic(master_dir):
    r = asyncio.run(svc.resolve_master([], svc.MASTER_DIR.parent))
    assert r.status == "ok"
    assert r.document_id is None
    assert r.master_path == master_dir / "_generic.history.json"


def test_resolve_non_excel_intercepts(master_dir, tmp_path, monkeypatch):
    xlsx = tmp_path / "a.pdf"
    xlsx.write_bytes(b"%PDF")
    _patch_classify(monkeypatch, [])
    r = asyncio.run(svc.resolve_master([_att("a.pdf", str(xlsx))], tmp_path))
    assert r.status == "intercept"
    assert "Excel" in r.intercept_message


def test_resolve_single_excel_ok(master_dir, tmp_path, monkeypatch):
    xlsx = tmp_path / "a.xlsx"
    xlsx.write_bytes(b"PK")
    _patch_classify(monkeypatch, [ClassificationResult(matched=True, document_id="D5")])
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(xlsx))], tmp_path))
    assert r.status == "ok"
    assert r.document_id == "D5"
    assert r.master_path == master_dir / "D5.history.json"


def test_resolve_unmatched_intercepts(master_dir, tmp_path, monkeypatch):
    xlsx = tmp_path / "a.xlsx"
    xlsx.write_bytes(b"PK")
    _patch_classify(monkeypatch, [ClassificationResult(matched=False, document_id=None)])
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(xlsx))], tmp_path))
    assert r.status == "intercept"
    assert "未识别" in r.intercept_message


def test_resolve_classifier_raises_intercepts(master_dir, tmp_path, monkeypatch):
    xlsx = tmp_path / "a.xlsx"
    xlsx.write_bytes(b"PK")

    async def boom(raw, ext):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(svc, "classify_file", boom)
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(xlsx))], tmp_path))
    assert r.status == "intercept"
    assert "未识别" in r.intercept_message


def test_resolve_multi_different_templates_intercepts(master_dir, tmp_path, monkeypatch):
    f1 = tmp_path / "a.xlsx"; f1.write_bytes(b"PK")
    f2 = tmp_path / "b.xlsx"; f2.write_bytes(b"PK")
    _patch_classify(monkeypatch, [
        ClassificationResult(matched=True, document_id="D5"),
        ClassificationResult(matched=True, document_id="D7"),
    ])
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(f1)), _att("b.xlsx", str(f2))], tmp_path))
    assert r.status == "intercept"
    assert "多份" in r.intercept_message


def test_resolve_multi_same_template_ok(master_dir, tmp_path, monkeypatch):
    f1 = tmp_path / "a.xlsx"; f1.write_bytes(b"PK")
    f2 = tmp_path / "b.xlsx"; f2.write_bytes(b"PK")
    _patch_classify(monkeypatch, [
        ClassificationResult(matched=True, document_id="D4"),
        ClassificationResult(matched=True, document_id="D4"),
    ])
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(f1)), _att("b.xlsx", str(f2))], tmp_path))
    assert r.status == "ok"
    assert r.document_id == "D4"


def test_resolve_master_file_missing_intercepts(tmp_path, monkeypatch):
    # registry missing D3 file
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    (tmp_path / "_generic.history.json").write_text("{}", encoding="utf-8")
    svc._build_registry()
    xlsx = tmp_path / "a.xlsx"; xlsx.write_bytes(b"PK")
    _patch_classify(monkeypatch, [ClassificationResult(matched=True, document_id="D3")])
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(xlsx))], tmp_path))
    assert r.status == "intercept"
    assert "母版尚未配置" in r.intercept_message


def test_resolve_read_bytes_fail_intercepts(master_dir, tmp_path, monkeypatch):
    # saved_path points to nonexistent file
    _patch_classify(monkeypatch, [])
    r = asyncio.run(svc.resolve_master([_att("a.xlsx", str(tmp_path / "nope.xlsx"))], tmp_path))
    assert r.status == "intercept"
    assert "读取失败" in r.intercept_message
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_template_prompt_service.py -q`
Expected: FAIL — `resolve_master` 不存在。

- [ ] **Step 3: 实现 `resolve_master`**

追加到 `app/services/template_prompt_service.py`：

```python
from app.services.template_classifier import classify_file

_EXCEL_EXTS = (".xlsx", ".xls")

_MSG_NON_EXCEL = "请上传岗位标准化模板 Excel 文件（.xlsx/.xls）"
_MSG_UNRECOGNIZED = "附件未识别为标准模板，请确认后重传"
_MSG_MULTI_TEMPLATE = "检测到多份不同模板附件，请说明你想用哪份进行辅导"
_MSG_MASTER_MISSING = "模板母版尚未配置，请联系管理员"
_MSG_READ_FAIL = "附件读取失败，请重传"


def _intercept(message: str, reason: str = "") -> Resolution:
    return Resolution(status="intercept", intercept_message=message, reason=reason)


async def resolve_master(attachments: list[dict], base_dir: Path) -> Resolution:
    """Decide which master to load for this turn based on attachments.

    attachments: items from _save_attachments (saved_path relative to base_dir
    or absolute). Returns ok+master_path or intercept+message.
    """
    if not attachments:
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_template_prompt_service.py -q`
Expected: PASS（全部，含 Task 3 的 4 项 + 本任务 9 项）。

- [ ] **Step 5: 提交**

```bash
git add app/services/template_prompt_service.py tests/unit/test_template_prompt_service.py
git commit -m "feat(master): implement resolve_master with intercept branches"
```

---

## Task 5: 会话创建移除母体预加载

**Files:**
- Modify: `app/api/v1/routes/sessions.py:74-81`
- Modify: `tests/integration/test_session_history_visibility.py`
- Modify: `tests/integration/test_persistence.py`（`test_context_messages_persisted`）

**Interfaces:**
- Consumes: 无新接口。
- Produces: 会话创建时 `session.messages` 为空（不含母体 context 消息）。母体改由 Task 6 每轮动态加载。

- [ ] **Step 1: 调整 `sessions.py` — 仅删除母体预加载，保留 materials**

删除 `app/api/v1/routes/sessions.py:74` 这一行（母体预加载）：

```python
    session.messages.extend(load_default_context_messages(CONTEXT_FILE, LOGGER))
```

**保留**紧随其后的 `load_materials_context_messages(...)` 段（materials 自动加载是独立功能，spec §3.3 只移除母体预加载）。

移除不再使用的 import：`load_default_context_messages`。保留 `load_materials_context_messages`、`_extract_attachment_excerpt`、`SUPPORTED_ATTACHMENT_EXTS`。`CONTEXT_FILE` 仍用于 `context_file=CONTEXT_FILE.name`（第 51/59/64 行），保留其 import。

> 说明：母体（`load_default_context_messages`）改由 Task 6/7 每轮按附件动态加载；materials（`load_materials_context_messages`）维持原行为不变。

- [ ] **Step 2: 调整 `test_session_history_visibility.py`**

会话创建不再注入 context 消息，故"flag true 时含 context"不再成立。改为断言"创建后无 context 消息"：

```python
def test_session_history_shows_context_when_flag_true(client):
    created = client.post("/api/sessions", json={"show_context_in_history": True})
    assert created.status_code == 200

    history = created.json()["history"]
    # 母体不再在会话创建时预加载；context 消息改由每轮按附件动态注入
    assert all(not msg.get("is_context", False) for msg in history)
```

- [ ] **Step 3: 调整 `test_persistence.py::test_context_messages_persisted`**

母体不再创建时持久化。改为验证"会话创建时无 context 消息"：

```python
def test_context_messages_persisted(client):
    """母体不再在会话创建时持久化为 context 消息（改为每轮动态加载）。"""
    resp = client.post("/api/v1/sessions", json={"show_context_in_history": True})
    session_id = resp.json()["session_id"]

    resp2 = client.get(f"/api/v1/sessions/{session_id}")
    history = resp2.json()["history"]
    context_msgs = [m for m in history if m.get("is_context")]
    assert context_msgs == []
```

- [ ] **Step 4: 运行这两个测试文件确认通过**

Run: `.venv/bin/python -m pytest tests/integration/test_session_history_visibility.py tests/integration/test_persistence.py -q`
Expected: `test_session_history_visibility` 全 PASS。`test_persistence.py` 中 `test_chat_message_persisted` / `test_session_history_from_db` 因 JSON-POST-to-Form-route 422 已是既有失败（与本任务无关）；`test_context_messages_persisted` 经本任务调整后 PASS。

- [ ] **Step 5: 提交**

```bash
git add app/api/v1/routes/sessions.py tests/integration/test_session_history_visibility.py tests/integration/test_persistence.py
git commit -m "refactor(session): drop 母体 preload at session creation; master loaded per-turn"
```

---

## Task 6: `_build_model_messages` 增 `master_messages` 参数

**Files:**
- Modify: `app/services/llm_service.py:11-32`
- Test: `tests/unit/test_llm_service.py`

**Interfaces:**
- Consumes: `app.services.context_service._clone_context_messages`（已存在，用于克隆母版消息）。
- Produces: `_build_model_messages(session, user_msg, master_messages=None) -> list[dict]`。`master_messages` 非 None 时作为 messages 前缀（替代硬编码 system 行）；None 时回退原硬编码行为（向后兼容）。

- [ ] **Step 1: 写失败测试 — 母版前缀 + None 兼容**

追加到 `tests/unit/test_llm_service.py`：

```python
from app.models.chat import ChatMessage, ChatSession
from app.services.llm_service import _build_model_messages


def _session():
    s = ChatSession(session_id="s1", show_context_in_history=False, context_file="ctx.json")
    s.messages.append(ChatMessage(role="user", content="历史问题"))
    return s


def test_build_model_messages_with_master_prefix_replaces_system_line():
    session = _session()
    user_msg = ChatMessage(role="user", content="当前问题")
    session.messages.append(user_msg)

    master = [
        ChatMessage(role="system", content="母版system", is_context=True),
        ChatMessage(role="user", content="母版user", is_context=True),
        ChatMessage(role="assistant", content="母版assistant", is_context=True),
    ]
    msgs = _build_model_messages(session, user_msg, master_messages=master)

    # master messages come first, verbatim
    assert msgs[0] == {"role": "system", "content": "母版system"}
    assert msgs[1] == {"role": "user", "content": "母版user"}
    assert msgs[2] == {"role": "assistant", "content": "母版assistant"}
    # hardcoded system line must NOT appear
    assert "岗位标准化 AI 教练" not in "".join(m["content"] for m in msgs)
    # current user msg is last
    assert msgs[-1] == {"role": "user", "content": "当前问题"}


def test_build_model_messages_none_master_falls_back_to_hardcoded_system():
    session = _session()
    user_msg = ChatMessage(role="user", content="当前问题")
    session.messages.append(user_msg)

    msgs = _build_model_messages(session, user_msg, master_messages=None)
    assert msgs[0]["role"] == "system"
    assert "岗位标准化 AI 教练" in msgs[0]["content"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_llm_service.py -q`
Expected: FAIL — `master_messages` 参数未实现（TypeError 或断言失败）。

- [ ] **Step 3: 实现 — 改 `_build_model_messages`**

替换 `app/services/llm_service.py:11-32` 整个函数：

```python
from app.services.context_service import _clone_context_messages

_DEFAULT_SYSTEM = (
    "你是岗位标准化 AI 教练。"
    "请在回答中保持教练式引导，优先围绕用户提供的上下文和材料。"
)


def _build_model_messages(
    session: ChatSession,
    user_msg: ChatMessage,
    master_messages: list[ChatMessage] | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []

    if master_messages is not None:
        for m in _clone_context_messages(master_messages):
            messages.append({"role": m.role, "content": m.content})
    else:
        messages.append({"role": "system", "content": _DEFAULT_SYSTEM})

    for msg in session.messages:
        if msg.role not in {"system", "user", "assistant"}:
            continue
        if msg is user_msg:
            continue
        messages.append({"role": msg.role, "content": msg.content})

    messages.append({"role": "user", "content": user_msg.content})
    return messages
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_llm_service.py -q`
Expected: PASS（含原 2 项 + 新 2 项）。

- [ ] **Step 5: 提交**

```bash
git add app/services/llm_service.py tests/unit/test_llm_service.py
git commit -m "feat(llm): _build_model_messages accepts master_messages prefix; fallback to default system"
```

---

## Task 7: `chat.py` 路由接入解析/拦截/加载

**Files:**
- Modify: `app/api/v1/routes/chat.py`（`/chat` 与 `/chat/stream`）
- Test: `tests/integration/test_chat_template_master.py`

**Interfaces:**
- Consumes:
  - `app.services.template_prompt_service.resolve_master(attachments, base_dir) -> Resolution`
  - `app.services.context_service.load_master_messages(master_path, logger) -> list[ChatMessage]`
  - `app.core.config.BASE_DIR`
- Produces: 两个路由在 `_append_user_message_with_attachments` 之后调用 `resolve_master`；intercept 则 `raise HTTPException(400, ...)`；否则 `load_master_messages` → 传 `_build_model_messages(..., master_messages)`。

- [ ] **Step 1: 写失败集成测试**

`tests/integration/test_chat_template_master.py`：

```python
import json
from io import BytesIO

import pytest

from app.services import template_prompt_service as svc
from app.services.template_classifier import ClassificationResult


@pytest.fixture(autouse=True)
def _masters(tmp_path, monkeypatch):
    """Point MASTER_DIR at a tmp dir with 8 master files."""
    monkeypatch.setattr(svc, "MASTER_DIR", tmp_path)
    for did in ["_generic", "D1", "D2", "D3", "D4", "D5", "D6", "D7"]:
        (tmp_path / f"{did}.history.json").write_text(json.dumps({
            "messages": [{"role": "system", "content": f"{did} master"}],
        }), encoding="utf-8")
    svc._build_registry()
    yield


def _patch_classify_d5(monkeypatch):
    async def fake(raw, ext):
        return ClassificationResult(matched=True, document_id="D5")
    monkeypatch.setattr(svc, "classify_file", fake)


def _patch_classify_unmatched(monkeypatch):
    async def fake(raw, ext):
        return ClassificationResult(matched=False, document_id=None)
    monkeypatch.setattr(svc, "classify_file", fake)


def _xlsx_bytes():
    from openpyxl import Workbook
    wb = Workbook(); wb.active["A1"] = "x"
    buf = BytesIO(); wb.save(buf); return buf.getvalue()


def test_chat_with_d5_attachment_uses_d5_master(client, monkeypatch):
    _patch_classify_d5(monkeypatch)
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]
    resp = client.post(
        "/api/chat",
        data={"session_id": sid, "message": "辅导我"},
        files={"files": ("a.xlsx", _xlsx_bytes(), "application/vnd.ms-excel")},
    )
    assert resp.status_code == 200
    # No assertion on reply content (OPENAI_API_KEY empty -> fallback text)


def test_chat_non_excel_attachment_intercepts(client, monkeypatch):
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]
    resp = client.post(
        "/api/chat",
        data={"session_id": sid, "message": "看这个"},
        files={"files": ("a.pdf", b"%PDF", "application/pdf")},
    )
    assert resp.status_code == 400
    assert "Excel" in resp.json()["detail"]


def test_chat_unmatched_attachment_intercepts(client, monkeypatch):
    _patch_classify_unmatched(monkeypatch)
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]
    resp = client.post(
        "/api/chat",
        data={"session_id": sid, "message": "看这个"},
        files={"files": ("a.xlsx", _xlsx_bytes(), "application/vnd.ms-excel")},
    )
    assert resp.status_code == 400
    assert "未识别" in resp.json()["detail"]


def test_chat_no_attachment_uses_generic(client, monkeypatch):
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]
    resp = client.post("/api/chat", data={"session_id": sid, "message": "你好"})
    assert resp.status_code == 200
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/integration/test_chat_template_master.py -q`
Expected: FAIL — 路由未接入 `resolve_master`（非 Excel 当前不报 400）。

- [ ] **Step 3: 实现路由接入 — 抽取公共函数 + 改两个路由**

在 `app/api/v1/routes/chat.py` 顶部 import 区追加：

```python
from app.core.config import BASE_DIR
from app.services.context_service import load_master_messages
from app.services.template_prompt_service import resolve_master
```

在 `_get_or_load_session` 函数下方新增辅助函数：

```python
async def _resolve_master_messages(
    user_msg: ChatMessage,
    logger,
) -> list[ChatMessage]:
    """Resolve the master prompt for this turn. Raises HTTPException on
    intercept; returns master message list (possibly empty if load fails)."""
    resolution = await resolve_master(user_msg.attachments, BASE_DIR)
    if resolution.status == "intercept":
        logger.info("template_intercept reason={}", resolution.reason)
        raise HTTPException(status_code=400, detail=resolution.intercept_message)
    master_messages = load_master_messages(resolution.master_path, LOGGER)
    if not master_messages:
        logger.error("master_load_empty path={}", resolution.master_path)
        raise HTTPException(
            status_code=400, detail="模板母版加载失败，请联系管理员"
        )
    return master_messages
```

改 `/chat`（原第 107-109 行）：

```python
    master_messages = await _resolve_master_messages(user_msg, chat_logger)
    llm_messages = _build_model_messages(session, user_msg, master_messages)
    _log_llm_payload_debug(chat_logger, llm_messages, user_msg)
    assistant_text = await _call_llm(llm_messages)
```

改 `/chat/stream`（原第 152-153 行）：同样在 `_append_user_message_with_attachments` 之后、`_build_model_messages` 之前插入 `master_messages = await _resolve_master_messages(user_msg, stream_logger)`，并传给 `_build_model_messages`。

- [ ] **Step 4: 运行集成测试确认通过**

Run: `.venv/bin/python -m pytest tests/integration/test_chat_template_master.py -q`
Expected: PASS（4 项）。

- [ ] **Step 5: 回归现有 chat 集成测试**

Run: `.venv/bin/python -m pytest tests/integration/test_chat_stream_done_payload.py tests/integration/test_llm_fallback_behavior.py -q`
Expected: PASS。这些测试无附件发消息 → 走 `_generic` 母版 → 200。

- [ ] **Step 6: 提交**

```bash
git add app/api/v1/routes/chat.py tests/integration/test_chat_template_master.py
git commit -m "feat(chat): resolve+intercept+load master per turn in /chat and /chat/stream"
```

---

## Task 8: 启动校验 + 全量回归

**Files:**
- Modify: `main.py`（lifespan 启动调用 `validate_master_registry`）

**Interfaces:**
- Consumes: `app.services.template_prompt_service.validate_master_registry`。

- [ ] **Step 1: 在 `main.py` lifespan 启动时调用校验**

在 `main.py` 的 `lifespan` 函数中，`yield` 之前加入：

```python
from app.services.template_prompt_service import validate_master_registry
from app.core.logger import get_component_logger
validate_master_registry(get_component_logger(component="chatbot"))
```

（`LOGGER` 已在模块顶层定义，可直接复用 `validate_master_registry(LOGGER)`。）

- [ ] **Step 2: 全量单测 + 集成回归**

Run:
```bash
.venv/bin/python -m pytest tests/unit/ tests/integration/test_chat_template_master.py tests/integration/test_session_history_visibility.py tests/integration/test_chat_stream_done_payload.py tests/integration/test_llm_fallback_behavior.py tests/integration/test_api_basics.py -q
```
Expected: 全 PASS（`test_persistence.py` 中两个 422 用例为既有问题，不在本计划范围，可单独 `--deselect` 或记录为已知问题）。

- [ ] **Step 3: 手动冒烟（可选）— 真实 LLM 验证 D5 流程**

如已配 `OPENAI_API_KEY`：
```bash
# 创建会话
curl -s -X POST localhost:2024/api/v1/sessions -H 'Content-Type: application/json' -d '{"show_context_in_history":false}'
# 上传 D5 模板附件发消息（用 tests/fixtures/classifier/cases 下任一 D5 文件）
curl -s -X POST localhost:2024/api/v1/chat -F session_id=<sid> -F message=辅导我 -F files=@tests/fixtures/classifier/cases/CBG产品经理.xlsx
```
Expected: 200 + 教练式回复；日志含 `master` 加载。

- [ ] **Step 4: 提交**

```bash
git add main.py
git commit -m "feat(main): validate master registry at startup"
```

---

## Self-Review 记录

**Spec coverage:** spec §2 决策→各任务对应；§3 架构→Task 1-3,5,6,7；§4 数据流→Task 4,7；§5 错误处理→Task 4,7,8；§6 测试→Task 2,3,4,6,7。覆盖完整。

**Placeholder scan:** 母版文本为占位（spec 明确"文本由用户后续提供"，非计划占位），已注明。

**Type consistency:** `Resolution`、`resolve_master`、`load_master_messages`、`_build_model_messages(master_messages=)` 在各任务签名一致；`classify_file` 复用现有签名。

**已知偏离（记录非 bug）：**
- `test_persistence.py` 中 `test_chat_message_persisted` / `test_session_history_from_db` 因 JSON-POST 到 Form 路由返回 422，是既有失败（本计划前已存在），不在本期范围。Task 8 Step 2 注明 deselect。
