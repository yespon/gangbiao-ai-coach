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

将现有 `load_default_context_messages`（`context_service.py:88-125`）重命名为 `load_master_messages(master_path: Path, logger)`，函数体里所有 `context_file` 改为 `master_path`。**并给 JSON 解析加 try/except**——缺失或损坏的母版必须返回 `[]`（不抛），否则 Task 7 的"加载后空列表则拦截"无法生效（spec §5 要求坏母版不致 500）。

```python
def load_master_messages(master_path: Path, logger) -> list[ChatMessage]:
    """Load a master prompt (OpenAI chat history JSON) as context messages.

    Returns [] when the file is missing or unparseable, so callers can
    intercept (HTTP 400) instead of crashing on a corrupt master.
    """
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


def load_default_context_messages(context_file: Path, logger) -> list[ChatMessage]:
    """Backward-compatible wrapper: load the default (D5) master as context."""
    return load_master_messages(context_file, logger)
```

保留 import 不变（`json`、`ChatMessage` 等已在文件顶部）。

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

---

## Phase B: 当前模板持久化（Task 9–11）

Revised spec (2026-06-29) adds `current_template_id` persistence so a user
who uploads a D5 template, then sends a plain-text follow-up, keeps using D5
instead of falling back to `_generic`. See spec §2, §4.1 step 3/6, §4.2, §4.5.

## Task 9: alembic 迁移 + DB 模型 + 内存模型

**Files:**
- Create: `alembic/versions/008_chat_session_current_template.py`
- Modify: `app/models/db_models.py`（`ChatSessionDB` 增列）
- Modify: `app/models/chat.py`（`ChatSession` 增字段）

**Interfaces:**
- Consumes: 现有 `ChatSessionDB` 列定义、`ChatSession.__init__` 参数。
- Produces: `ChatSessionDB.current_template_id: Mapped[str | None]`、`ChatSession.current_template_id: str | None`（创建空会话时不设值，由后续 `rebuild_memory_session` / 路由更新填入）。

- [ ] **Step 1: 创建迁移文件**

`alembic/versions/008_chat_session_current_template.py`：

```python
"""Add current_template_id to chat_sessions.

Revision ID: 008
Revises: 007_session_title_pin_delete
"""

from alembic import op
import sqlalchemy as sa


revision = "008_chat_session_current_template"
down_revision = "007_session_title_pin_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("current_template_id", sa.String(10), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_sessions", "current_template_id")
```

- [ ] **Step 2: 验证迁移在本地 DB 可执行（requires_pg 环境）**

Run: `.venv/bin/python -m pytest tests/integration/test_persistence.py -q -k "test_chat" --no-header 2>&1 | tail -3`
（使用已有 `requires_pg` 标记的测试验证 DB 连接可用。迁移本身通过 alembic 手动验证。）

手动验证（如有本地 PG）：
```bash
.venv/bin/alembic upgrade head && .venv/bin/alembic downgrade -1 && .venv/bin/alembic upgrade head
```
Expected: 无错误。

- [ ] **Step 3: 改 `app/models/db_models.py` — `ChatSessionDB` 增列**

在 `ChatSessionDB` 类中 `deleted_at` 定义之后、`# -- relationships --` 注释之前，插入：

```python
    current_template_id: Mapped[str | None] = mapped_column(String(10), nullable=True)
```

- [ ] **Step 4: 改 `app/models/chat.py` — `ChatSession` 增字段**

```python
@dataclass
class ChatSession:
    session_id: str
    show_context_in_history: bool
    context_file: str
    user_id: str = "anonymous"
    created_at: str = field(default_factory=_now_iso)
    messages: list[ChatMessage] = field(default_factory=list)
    current_template_id: str | None = None
```

- [ ] **Step 5: 验证 `import` 无错误**

Run: `.venv/bin/python -c "from app.models.db_models import ChatSessionDB; print('DB OK'); from app.models.chat import ChatSession; s=ChatSession(session_id='x',show_context_in_history=False,context_file=''); print('memory OK'); print(s.current_template_id)"`
Expected: `DB OK` / `memory OK` / `None`。

- [ ] **Step 6: 提交**

```bash
git add alembic/versions/008_chat_session_current_template.py app/models/db_models.py app/models/chat.py
git commit -m "feat(db): add current_template_id column to chat_sessions; add memory field"
```

---

## Task 10: `rebuild_memory_session` 回填 + `update_session_template` + `resolve_master` 签名变更

**Files:**
- Modify: `app/services/session_service.py`（`rebuild_memory_session` 回填 + 新增 `update_session_template`）
- Modify: `app/services/template_prompt_service.py`（`resolve_master` 增 `current_template_id` 参数，无附件分支改逻辑）
- Test: `tests/unit/test_template_prompt_service.py`（追加 2 个沿用模板的测试）
- Test: `tests/unit/test_session_service_master.py`（新建，测 `update_session_template` + `rebuild` 回填）

**Interfaces:**
- Consumes: `sqlalchemy.ext.asyncio.AsyncSession`、`app.models.db_models.ChatSessionDB`、`app.models.chat.ChatSession.current_template_id`（Task 9）。
- Produces:
  - `rebuild_memory_session` 填充 `current_template_id=getattr(session_db, "current_template_id", None) or None`
  - `async update_session_template(db, session_id, template_id) -> None`：`UPDATE chat_sessions SET current_template_id = :tid WHERE id = :sid`
  - `resolve_master(attachments, base_dir, current_template_id=None)`：无附件时若 `current_template_id` 非空 → `ok` + 对应母版；否则 → `ok, _generic`。**旧调用方（无第三参数）行为不变**（默认 None → 通用母版）。

- [ ] **Step 1: 新建 `tests/unit/test_session_service_master.py` — 失败测试**

```python
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.session_service import update_session_template


class _FakeSessionDB:
    def __init__(self):
        self.current_template_id = None
        self.id = "fake-sid"
        self.show_context = False
        self.context_file = ""
        self.user_id = "u1"
        self.created_at = None
        self.messages = []


async def test_update_session_template_sets_column():
    db = AsyncMock()
    
    # simulate get_session_by_id returning a session
    async def fake_get_session_by_id(db, session_id, user_id):
        s = MagicMock()
        s.id = "fake-sid"
        s.current_template_id = None
        return s

    with patch("app.services.session_service.get_session_by_id", new=fake_get_session_by_id):
        await update_session_template(db, "fake-sid", "D5")
    
    db.commit.assert_awaited_once()
```

注：`update_session_template` 需 `get_session_by_id` 查找会话（或直接用 SQL `UPDATE`）。更简单的方式是直接 `UPDATE`（不需要整个 ORM 对象），可写成：

```python
from sqlalchemy import update
from app.models.db_models import ChatSessionDB

async def update_session_template(
    db: AsyncSession, session_id: str, template_id: str
) -> None:
    await db.execute(
        update(ChatSessionDB)
        .where(ChatSessionDB.id == session_id)
        .values(current_template_id=template_id)
    )
    await db.commit()
```

这样可以避免异步 `get_session_by_id`，测试更简单——直接 mock `db.execute` + `db.commit`。

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv/bin/python -m pytest tests/unit/test_session_service_master.py -q`
Expected: FAIL — `update_session_template` 不存在。

- [ ] **Step 3: 实现 `update_session_template`**

追加到 `app/services/session_service.py`：

```python
from sqlalchemy import update as sa_update

async def update_session_template(
    db: AsyncSession, session_id: str, template_id: str
) -> None:
    """Persist the current template_id on a session (D1..D7). Called when
    classify_file successfully identifies an attachment."""
    await db.execute(
        sa_update(ChatSessionDB)
        .where(ChatSessionDB.id == session_id)
        .values(current_template_id=template_id)
    )
    await db.commit()
```

`sa_update` 需与文件顶部已存在的 `select` 区分。实际用 `from sqlalchemy import update as sa_update`（避免与 `select` 冲突）。

- [ ] **Step 4: 改 `rebuild_memory_session` 回填 `current_template_id`**

在 `ChatSession(...)` 构造函数中追加：

```python
        current_template_id=getattr(session_db, "current_template_id", None) or None,
```

放在 `created_at=...` 行之后。

- [ ] **Step 5: 运行 `test_session_service_master` 确认通过**

Run: `.venv/bin/python -m pytest tests/unit/test_session_service_master.py -q`
Expected: PASS。

- [ ] **Step 6: 追加 2 个 `resolve_master` 测试 + 改实现**

追加到 `tests/unit/test_template_prompt_service.py`（在现有 `test_resolve_no_attachments_returns_generic` 之后）：

```python
def test_resolve_no_attachments_with_current_template_returns_existing(master_dir):
    """当会话已有 current_template="D5" 时，无附件消息沿用 D5（不回退通用母版）。"""
    r = asyncio.run(svc.resolve_master([], svc.MASTER_DIR.parent, current_template_id="D5"))
    assert r.status == "ok"
    assert r.document_id == "D5"
    assert r.master_path == master_dir / "D5.history.json"


def test_resolve_no_attachments_current_template_none_returns_generic(master_dir):
    """current_template 为 None（新会话/通用态）时，无附件消息用通用母版（向后兼容）。"""
    r = asyncio.run(svc.resolve_master([], svc.MASTER_DIR.parent, current_template_id=None))
    assert r.status == "ok"
    assert r.document_id is None
    assert r.master_path == master_dir / "_generic.history.json"
```

- [ ] **Step 7: 确认新测试 FAIL**

Run: `.venv/bin/python -m pytest tests/unit/test_template_prompt_service.py -q -k "current_template"`
Expected: FAIL — `resolve_master` 不接受 `current_template_id` 参数。

- [ ] **Step 8: 改 `resolve_master` 签名 + 实现**

改 `resolve_master` 签名行（`template_prompt_service.py:72`）：

```python
async def resolve_master(
    attachments: list[dict], base_dir: Path,
    current_template_id: str | None = None,
) -> Resolution:
```

改无附件分支（`template_prompt_service.py:78-82`）：

```python
    if not attachments:
        doc_id = current_template_id
        path = get_master_path(current_template_id)
        if path is None:
            # current_template_id invalid (e.g. master file missing) — fall back to generic
            path = get_master_path(None)
            doc_id = None
            if path is None:
                return _intercept(_MSG_MASTER_MISSING, "generic master missing")
        return Resolution(status="ok", master_path=path, document_id=doc_id)
```

- [ ] **Step 9: 确认所有测试 PASS**

Run: `.venv/bin/python -m pytest tests/unit/test_template_prompt_service.py -q`
Expected: PASS（15 项：Task 3 的 4 + Task 4 的 9 + 本任务 2）。

- [ ] **Step 10: 提交**

```bash
git add app/services/session_service.py app/services/template_prompt_service.py tests/unit/test_template_prompt_service.py tests/unit/test_session_service_master.py
git commit -m "feat(master): persist current_template_id; resolve_master reuses it on no-attachment"
```

---

## Task 11: `chat.py` 路由写入 `current_template_id` + 完整测试

**Files:**
- Modify: `app/api/v1/routes/chat.py`（`_resolve_master_messages` 增参数，两路由写状态）
- Test: `tests/integration/test_chat_template_master.py`（追加 2 个集成测试：纯文本沿用 + 模板切换）
- Test: `tests/unit/test_template_prompt_service.py` 的 `resolve_master` 测试已覆盖（Task 10）

**Interfaces:**
- Consumes:
  - `app.services.session_service.update_session_template(db, session_id, template_id) -> None`（Task 10）
  - `app.services.template_prompt_service.resolve_master(attachments, base_dir, current_template_id)`（签名已改，Task 10）
  - `ChatSession.current_template_id`（Task 9）
- Produces: `/chat` 与 `/chat/stream` 识别成功后将 `document_id`（D1..D7）写入 DB + 内存。

**Global Constraints（Phase B 修订版）：**
- 无附件时沿用 `session.current_template_id`；仅当其为空时才用 `_generic`。
- 写入时机：仅带附件且识别成功时更新；无附件不写；拦截不写。
- `update_session_template` 调用条件：`resolution.status=="ok"` 且 `resolution.document_id` 为 D1..D7 且与 `session.current_template_id` 不同。DB 写入在 `db is not None` 时才执行（兼容测试的 `db=None` 模式）。

- [ ] **Step 1: 追加集成测试到 `tests/integration/test_chat_template_master.py`**

在现有 test 文件末尾追加：

```python
def test_chat_d5_then_text_follow_up_keeps_d5(client, monkeypatch):
    """上传 D5 后，纯文本追问仍用 D5 母版（current_template_id 持久化）。"""
    _patch_classify_d5(monkeypatch)
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]

    # 第一轮：上传 D5
    resp1 = client.post(
        "/api/chat",
        data={"session_id": sid, "message": "辅导我"},
        files={"files": ("a.xlsx", _xlsx_bytes(), "application/vnd.ms-excel")},
    )
    assert resp1.status_code == 200

    # 第二轮：纯文本（无附件）—— 应沿用 D5，不回退 generic
    resp2 = client.post("/api/chat", data={"session_id": sid, "message": "继续"})
    assert resp2.status_code == 200
    # 在 db=None 的测试环境下，current_template_id 仅保存在内存 ChatSession 上。
    # 验证：第二轮 resolve_master 应拿到 current_template_id="D5"。
    # 通过 monkeypatch 抓取 resolve_master 调用参数来验证。


def test_chat_d5_then_switch_to_d4(client, monkeypatch):
    """上传 D5 后上传 D4 附件，模板切换到 D4。"""
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]

    # Round 1: D5
    _patch_classify_d5(monkeypatch)
    resp1 = client.post(
        "/api/chat",
        data={"session_id": sid, "message": "辅导我"},
        files={"files": ("a.xlsx", _xlsx_bytes(), "application/vnd.ms-excel")},
    )
    assert resp1.status_code == 200

    # Round 2: D4 (switch)
    def _patch_classify_d4(m):
        async def fake(raw, ext):
            return ClassificationResult(matched=True, document_id="D4")
        m.setattr(svc, "classify_file", fake)

    _patch_classify_d4(monkeypatch)
    resp2 = client.post(
        "/api/chat",
        data={"session_id": sid, "message": "换模板了"},
        files={"files": ("b.xlsx", _xlsx_bytes(), "application/vnd.ms-excel")},
    )
    assert resp2.status_code == 200

    # Round 3: text follow-up — should now use D4
    resp3 = client.post("/api/chat", data={"session_id": sid, "message": "继续"})
    assert resp3.status_code == 200
```

- [ ] **Step 2: 运行测试确认 FAIL**

Run: `.venv/bin/python -m pytest tests/integration/test_chat_template_master.py -q`
Expected: 有两新测试中至少"纯文本沿用" FAIL（当前无附件仍回退 `_generic`，不会报错但模板不对——集成测试难以断言模板内容因 `OPENAI_API_KEY` 为空；但可通过抓 `resolve_master` 调用参数来验证）。

> **测试策略**：因集成测试环境 `OPENAI_API_KEY` 为空，无法从 LLM 回复中判断模板。改为在追加测试中使用 `monkeypatch` 抓取 `_resolve_master_messages` 的调用参数，或直接抓 `resolve_master` 返回值。最简单：追加一个 spy 收集 `resolve_master(master_dir, ...)` 调用写日志，然后在测试断言 `spy.current_template` == "D5"。

为简单可用 monkeypatch wrap `svc.resolve_master` 记录调用参数：

```python
def test_chat_d5_then_text_follow_up_keeps_d5(client, monkeypatch):
    _patch_classify_d5(monkeypatch)
    created = client.post("/api/sessions", json={"show_context_in_history": False})
    sid = created.json()["session_id"]

    calls = []
    original = svc.resolve_master
    async def spy(att, base_dir, current_template_id=None):
        calls.append(current_template_id)
        return await original(att, base_dir, current_template_id=current_template_id)
    monkeypatch.setattr(svc, "resolve_master", spy)

    # Round 1: D5 attachment
    client.post("/api/chat", data={"session_id": sid, "message": "辅导我"},
                files={"files": ("a.xlsx", _xlsx_bytes(), "application/vnd.ms-excel")})
    # Round 2: text only
    client.post("/api/chat", data={"session_id": sid, "message": "继续"})
    # Round 2 should have called resolve_master with current_template_id="D5"
    assert calls[-1] == "D5", f"expected D5, got {calls[-1]} (all calls: {calls})"
```

- [ ] **Step 3: 改 `_resolve_master_messages` 签名 + 传参**

在 `app/api/v1/routes/chat.py` 中改 `_resolve_master_messages` 签名：

```python
async def _resolve_master_messages(
    user_msg: ChatMessage,
    logger,
    current_template_id: str | None = None,
) -> list[ChatMessage]:
```

内部 `resolve_master` 调用改为：

```python
    resolution = await resolve_master(user_msg.attachments, BASE_DIR, current_template_id)
```

（第三参数传给 `resolve_master`）。

- [ ] **Step 4: 改 `/chat` 路由 — 传 `current_template_id` + 写状态**

改 `/chat` 路由中 `_resolve_master_messages` 调用行：

```python
    master_messages = await _resolve_master_messages(user_msg, chat_logger, session.current_template_id)
```

在 `_resolve_master_messages` 返回后、`_build_model_messages` 之前，插入状态同步：

```python
    # Persist template change
    resolution = await resolve_master(user_msg.attachments, BASE_DIR, session.current_template_id)
    if resolution.status == "intercept":
        ...
    # -- TOO COMPLEX: rewrite _resolve_master_messages to also return the resolution.
```

**更干净的方案**：改 `_resolve_master_messages` 返回 `(master_messages, new_template_id)`——`new_template_id` 为 `resolution.document_id`（仅当 D1..D7 时非空），路由据此写状态。

重写 `_resolve_master_messages`：

```python
async def _resolve_master_messages(
    user_msg: ChatMessage,
    logger,
    current_template_id: str | None = None,
) -> tuple[list[ChatMessage], str | None]:
    """Resolve master messages + return the template_id to persist (or None).

    Raises HTTPException(400) on intercept or empty-master-load.
    """
    resolution = await resolve_master(user_msg.attachments, BASE_DIR, current_template_id)
    if resolution.status == "intercept":
        logger.info("template_intercept reason={}", resolution.reason)
        raise HTTPException(status_code=400, detail=resolution.intercept_message)
    master_messages = load_master_messages(resolution.master_path, logger)
    if not master_messages:
        logger.error("master_load_empty path={}", resolution.master_path)
        raise HTTPException(
            status_code=400, detail="模板母版加载失败，请联系管理员"
        )
    new_template = resolution.document_id  # D1..D7, or None for _generic
    return master_messages, new_template
```

改 `/chat` 路由调用点：

```python
    master_messages, new_template = await _resolve_master_messages(
        user_msg, chat_logger, session.current_template_id)
    if new_template and new_template != session.current_template_id:
        session.current_template_id = new_template
        if db is not None:
            await update_session_template(db, session_id, new_template)
    llm_messages = _build_model_messages(session, user_msg, master_messages)
```

改 `/chat/stream` 对应位置（同模式）。

`update_session_template` 从 `app/services/session_service` import（追加到 chat.py 头部）。

- [ ] **Step 5: 运行测试确认通过**

Run: `.venv/bin/python -m pytest tests/integration/test_chat_template_master.py -q`
Expected: PASS（6 项：Task 7 的 4 + 本任务 2）。

- [ ] **Step 6: 回归全部**

Run: `.venv/bin/python -m pytest tests/unit/ --ignore=tests/unit/test_upload_size_limit.py tests/integration/test_chat_template_master.py tests/integration/test_session_history_visibility.py tests/integration/test_chat_stream_done_payload.py tests/integration/test_llm_fallback_behavior.py tests/integration/test_api_basics.py -q`
Expected: 全 PASS。

- [ ] **Step 7: 提交**

```bash
git add app/api/v1/routes/chat.py tests/integration/test_chat_template_master.py
git commit -m "feat(chat): persist and reuse current_template_id across turns"
```

---

## Phase B Self-Review

**Spec coverage:** §2 无附件沿用 + 持久化 → Task 9（DB 列）、Task 10（resolve_master + update_session_template）、Task 11（路由写入 + 集成测试）。§4.1 step 6 → Task 11。§4.2 无附件分支 → Task 10。§4.5 全项 → Task 9+10+11。§6.1 新增 2 resolve_master 测试 → Task 10。§6.2 纯文本沿用 + 切换 → Task 11。§6.4 DB 测试 → Task 9+10。覆盖完整。

**Placeholder scan:** 无 TBD/TODO。所有步骤含完整代码。

**Type consistency:**
- `resolve_master(attachments, base_dir, current_template_id=None)` — Task 10 定义，Task 11 调用端一致。
- `_resolve_master_messages(user_msg, logger, current_template_id=None) -> tuple[list[ChatMessage], str | None]` — Task 11 定义，两路由调用端一致（tuple 解包 `master_messages, new_template`）。
- `update_session_template(db, session_id, template_id)` — Task 10 实现，Task 11 import + 调用一致。
- `session.current_template_id` — Task 9 内存模型字段，Task 10/11 读写一致。
- `ChatSessionDB.current_template_id` — Task 9 DB 列，Task 10 `rebuild_memory_session`/`update_session_template` 读写一致。
