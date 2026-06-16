# Admin Panel User Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent `/admin` backend that upgrades SSO whitelist into managed user profiles and adds role-filtered conversation history viewing.

**Architecture:** Add a new `managed_users` profile table as the system access-control and business-identity source, while keeping `users` as the login table and preserving `chat_sessions.user_id`. Backend services own role normalization, Excel import, access checks, and conversation aggregation; frontend `/admin` pages consume those APIs through focused TypeScript clients.

**Tech Stack:** FastAPI, SQLAlchemy async ORM, Alembic, PostgreSQL, openpyxl, pytest, Next.js App Router, React 18, TypeScript.

---

## File Structure

Backend files:

- Create: `alembic/versions/005_managed_users.py` — create `managed_users`, add `users.managed_user_id`, migrate existing whitelist data, backfill CAS users.
- Modify: `app/models/db_models.py` — add `ManagedUserDB` ORM model and `User.managed_user_id` relationship.
- Create: `app/services/managed_user_service.py` — managed user normalization, role rules, Excel template/import parsing, upsert, coach validation, login access helpers.
- Create: `app/services/admin_conversation_service.py` — conversation visibility checks and read-only aggregation queries.
- Modify: `app/services/user_service.py` — link SSO logins to `managed_users`.
- Modify: `app/api/v1/routes/cas.py` — change CAS access gate from whitelist table to managed users.
- Modify: `app/api/v1/routes/admin.py` — keep `require_admin`, add users and conversations endpoints, retain old whitelist endpoints until callers move.
- Modify: `app/models/schema.py` — enrich `UserResponse` with managed profile fields needed by the admin UI.

Backend tests:

- Create: `tests/unit/test_managed_user_service.py` — role normalization, template headers, import parsing, system-admin protection.
- Create: `tests/unit/test_admin_conversation_service.py` — conversation visibility helper behavior.
- Create: `tests/integration/test_admin_users_api.py` — admin users API permissions and responses using dependency overrides.
- Create: `tests/integration/test_admin_conversations_api.py` — conversation API permission filtering using fake DB/service seams.
- Modify: `tests/integration/test_admin_whitelist_api.py` — adjust route coverage to new user template while preserving admin-required checks.
- Modify: `tests/unit/test_user_service_admin.py` or create it if absent — SSO user links to managed profile.

Frontend files:

- Modify: `frontend/types/auth.ts` — expose managed profile and role fields on `UserInfo`.
- Modify: `frontend/types/admin.ts` — replace whitelist-only types with managed user and conversation types.
- Modify: `frontend/lib/admin.ts` — add `/admin/users*` and `/admin/conversations*` clients.
- Create: `frontend/app/admin/layout.tsx` — independent admin shell with left navigation and top actions.
- Create: `frontend/app/admin/page.tsx` — simple admin overview.
- Create: `frontend/app/admin/users/page.tsx` — user management list, import, add/edit dialog.
- Create: `frontend/app/admin/conversations/page.tsx` — user summary, session list, read-only detail.
- Replace or remove: `frontend/app/admin/whitelist/page.tsx` — redirect to `/admin/users` or delete after routes move.
- Modify: `frontend/app/page.tsx` — admin menu entry points to `/admin`.
- Modify: `frontend/app/globals.css` — admin shell, tables, dialog, badges, conversation detail styles.

Validation commands:

- Backend targeted: `uv run pytest tests/unit/test_managed_user_service.py tests/unit/test_admin_conversation_service.py tests/integration/test_admin_users_api.py tests/integration/test_admin_conversations_api.py -q`
- Backend regression: `uv run pytest tests/unit tests/integration -q`
- Frontend type/build: `npm --prefix frontend run build`
- Manual UI: run backend/frontend and verify `/admin`, `/admin/users`, `/admin/conversations` in browser.

---

### Task 1: Add managed user data model and migration

**Files:**
- Create: `alembic/versions/005_managed_users.py`
- Modify: `app/models/db_models.py:14-39`
- Test: `tests/unit/test_managed_user_service.py`

- [ ] **Step 1: Write failing unit tests for role helpers that the model will use**

Create `tests/unit/test_managed_user_service.py` with:

```python
from io import BytesIO

from openpyxl import load_workbook

from app.services.managed_user_service import (
    MANAGED_USER_TEMPLATE_HEADERS,
    is_effective_coach,
    normalize_managed_user_role,
    protect_system_admin_patch,
    build_managed_user_template,
)


def test_normalize_student_role_clears_coach_fields():
    normalized = normalize_managed_user_role("学员", True, "coach-id")
    assert normalized == {"primary_role": "student", "is_coach": False, "coach_id": "coach-id"}


def test_normalize_coach_role_forces_is_coach_and_clears_coach_id():
    normalized = normalize_managed_user_role("教练", False, "coach-id")
    assert normalized == {"primary_role": "coach", "is_coach": True, "coach_id": None}


def test_normalize_admin_role_keeps_admin_coach_capability_and_clears_coach_id():
    normalized = normalize_managed_user_role("管理员", True, "coach-id")
    assert normalized == {"primary_role": "admin", "is_coach": True, "coach_id": None}


def test_effective_coach_includes_coach_and_admin_coach():
    assert is_effective_coach("coach", False) is True
    assert is_effective_coach("admin", True) is True
    assert is_effective_coach("admin", False) is False
    assert is_effective_coach("student", False) is False


def test_system_admin_patch_cannot_disable_or_downgrade():
    protected = protect_system_admin_patch(
        employee_no="1001",
        admin_employee_nos={"1001"},
        requested={"enabled": False, "primary_role": "student", "is_coach": False},
    )
    assert protected["enabled"] is True
    assert protected["primary_role"] == "admin"
    assert protected["is_coach"] is False


def test_managed_user_template_headers_are_in_confirmed_order():
    wb = load_workbook(BytesIO(build_managed_user_template()))
    assert [cell.value for cell in wb.active[1]] == MANAGED_USER_TEMPLATE_HEADERS
    assert MANAGED_USER_TEMPLATE_HEADERS == [
        "工号",
        "姓名",
        "邮箱",
        "一级部门",
        "主角色",
        "兼任教练",
        "所属教练工号",
        "启用状态",
    ]
```

- [ ] **Step 2: Run tests to verify the new service is missing**

Run:

```bash
uv run pytest tests/unit/test_managed_user_service.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.managed_user_service'`.

- [ ] **Step 3: Add the ORM model and user relationship**

In `app/models/db_models.py`, update imports and add `ManagedUserDB` before `User`:

```python
from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, text
```

```python
class ManagedUserDB(Base):
    __tablename__ = "managed_users"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    employee_no: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    department_level1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    primary_role: Mapped[str] = mapped_column(String(20), server_default=text("'student'"), index=True)
    is_coach: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    coach_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("managed_users.id", ondelete="SET NULL"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), index=True)
    source: Mapped[str] = mapped_column(String(20), server_default=text("'manual'"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=text("now()"), onupdate=datetime.now(UTC)
    )

    coach: Mapped["ManagedUserDB | None"] = relationship(remote_side=[id])
```

Inside `User`, add after `provider_user_id`:

```python
    managed_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("managed_users.id", ondelete="SET NULL"), nullable=True, index=True
    )
```

Add after `sessions` relationship:

```python
    managed_user: Mapped["ManagedUserDB | None"] = relationship()
```

- [ ] **Step 4: Add Alembic migration**

Create `alembic/versions/005_managed_users.py`:

```python
"""managed users

Revision ID: 005_managed_users
Revises: 004_sso_user_whitelist
Create Date: 2026-06-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

revision: str = "005_managed_users"
down_revision: Union[str, Sequence[str], None] = "004_sso_user_whitelist"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "managed_users",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("employee_no", sa.String(100), nullable=False),
        sa.Column("name", sa.String(100), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("department_level1", sa.String(255), nullable=True),
        sa.Column("primary_role", sa.String(20), server_default=sa.text("'student'"), nullable=False),
        sa.Column("is_coach", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("coach_id", UUID(as_uuid=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("source", sa.String(20), server_default=sa.text("'manual'"), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["coach_id"], ["managed_users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_managed_users_employee_no", "managed_users", ["employee_no"], unique=True)
    op.create_index("ix_managed_users_enabled", "managed_users", ["enabled"])
    op.create_index("ix_managed_users_primary_role", "managed_users", ["primary_role"])

    op.add_column("users", sa.Column("managed_user_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_users_managed_user_id", "users", ["managed_user_id"])
    op.create_foreign_key(
        "fk_users_managed_user_id_managed_users",
        "users",
        "managed_users",
        ["managed_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.execute(
        """
        INSERT INTO managed_users (employee_no, email, enabled, primary_role, is_coach, source, created_by, created_at, updated_at)
        SELECT employee_no, email, enabled, 'student', false, 'migrated', created_by, created_at, updated_at
        FROM sso_user_whitelist
        ON CONFLICT (employee_no) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE users
        SET managed_user_id = managed_users.id
        FROM managed_users
        WHERE users.provider = 'cas'
          AND users.provider_user_id = managed_users.employee_no
          AND users.managed_user_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_managed_user_id_managed_users", "users", type_="foreignkey")
    op.drop_index("ix_users_managed_user_id", table_name="users")
    op.drop_column("users", "managed_user_id")
    op.drop_index("ix_managed_users_primary_role", table_name="managed_users")
    op.drop_index("ix_managed_users_enabled", table_name="managed_users")
    op.drop_index("ix_managed_users_employee_no", table_name="managed_users")
    op.drop_table("managed_users")
```

- [ ] **Step 5: Add minimal managed user service helpers**

Create `app/services/managed_user_service.py`:

```python
from dataclasses import dataclass
from io import BytesIO
from typing import Any
import uuid

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db_models import ManagedUserDB
from app.services.whitelist_service import WHITELIST_DENY_MESSAGE, MAX_WHITELIST_ROWS, MAX_WHITELIST_UPLOAD_BYTES

ROLE_LABELS = {"管理员": "admin", "教练": "coach", "学员": "student", "admin": "admin", "coach": "coach", "student": "student"}
ENABLED_LABELS = {"启用": True, "禁用": False, "true": True, "false": False, "是": True, "否": False}
MANAGED_USER_TEMPLATE_HEADERS = ["工号", "姓名", "邮箱", "一级部门", "主角色", "兼任教练", "所属教练工号", "启用状态"]


@dataclass
class ManagedUserParseResult:
    rows: list[dict[str, Any]]
    errors: list[dict[str, Any]]


def _cell_text(value) -> str:
    return str(value).strip() if value is not None else ""


def normalize_employee_no(value: str) -> str:
    return value.strip()


def is_effective_coach(primary_role: str, is_coach: bool) -> bool:
    return primary_role == "coach" or (primary_role == "admin" and is_coach)


def normalize_managed_user_role(primary_role: str | None, is_coach: bool, coach_id):
    role = ROLE_LABELS.get((primary_role or "学员").strip())
    if role is None:
        raise ValueError("主角色必须是管理员、教练或学员")
    if role == "coach":
        return {"primary_role": "coach", "is_coach": True, "coach_id": None}
    if role == "admin":
        return {"primary_role": "admin", "is_coach": bool(is_coach), "coach_id": None}
    return {"primary_role": "student", "is_coach": False, "coach_id": coach_id}


def protect_system_admin_patch(employee_no: str, admin_employee_nos: set[str], requested: dict[str, Any]) -> dict[str, Any]:
    if employee_no not in admin_employee_nos:
        return requested
    protected = dict(requested)
    protected["enabled"] = True
    protected["primary_role"] = "admin"
    return protected


def build_managed_user_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "用户管理"
    ws.append(MANAGED_USER_TEMPLATE_HEADERS)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

- [ ] **Step 6: Run tests to verify helper behavior passes**

Run:

```bash
uv run pytest tests/unit/test_managed_user_service.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/005_managed_users.py app/models/db_models.py app/services/managed_user_service.py tests/unit/test_managed_user_service.py
git commit -m "feat: add managed user data model"
```

---

### Task 2: Implement managed user import parsing and upsert rules

**Files:**
- Modify: `app/services/managed_user_service.py`
- Test: `tests/unit/test_managed_user_service.py`

- [ ] **Step 1: Add failing parser tests**

Append to `tests/unit/test_managed_user_service.py`:

```python
from openpyxl import Workbook

from app.services.managed_user_service import parse_managed_user_excel, resolve_import_coach_links


def _xlsx(rows):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_managed_user_excel_defaults_student_and_enabled():
    data = _xlsx([
        ["工号", "姓名", "邮箱", "一级部门", "主角色", "兼任教练", "所属教练工号", "启用状态"],
        [" 1001 ", "张三", "a@example.com", "研发", None, None, None, None],
    ])
    result = parse_managed_user_excel(data)
    assert result.errors == []
    assert result.rows == [{
        "employee_no": "1001",
        "name": "张三",
        "email": "a@example.com",
        "department_level1": "研发",
        "primary_role": "student",
        "is_coach": False,
        "coach_employee_no": None,
        "enabled": True,
    }]


def test_parse_managed_user_excel_keeps_department_after_email():
    data = _xlsx([
        ["工号", "姓名", "邮箱", "一级部门", "主角色", "兼任教练", "所属教练工号", "启用状态"],
        ["2001", "李教练", "coach@example.com", "销售", "教练", "否", None, "启用"],
    ])
    result = parse_managed_user_excel(data)
    assert result.rows[0]["department_level1"] == "销售"
    assert result.rows[0]["primary_role"] == "coach"
    assert result.rows[0]["is_coach"] is True


def test_parse_managed_user_excel_rejects_invalid_student_coach_flag():
    data = _xlsx([
        ["工号", "姓名", "邮箱", "一级部门", "主角色", "兼任教练", "所属教练工号", "启用状态"],
        ["1001", "张三", None, None, "学员", "是", None, "启用"],
    ])
    result = parse_managed_user_excel(data)
    assert result.rows == []
    assert result.errors == [{"row": 2, "reason": "学员不能兼任教练"}]


def test_resolve_import_coach_links_accepts_same_batch_coach():
    rows = [
        {"employee_no": "2001", "primary_role": "coach", "is_coach": True, "coach_employee_no": None},
        {"employee_no": "1001", "primary_role": "student", "is_coach": False, "coach_employee_no": "2001"},
    ]
    errors = resolve_import_coach_links(rows, existing_coach_employee_nos=set())
    assert errors == []


def test_resolve_import_coach_links_rejects_missing_coach():
    rows = [
        {"employee_no": "1001", "primary_role": "student", "is_coach": False, "coach_employee_no": "9999"},
    ]
    errors = resolve_import_coach_links(rows, existing_coach_employee_nos=set())
    assert errors == [{"row": 2, "reason": "所属教练工号不存在或不是教练"}]
```

- [ ] **Step 2: Run parser tests and verify failure**

Run:

```bash
uv run pytest tests/unit/test_managed_user_service.py -q
```

Expected: FAIL with missing `parse_managed_user_excel` or `resolve_import_coach_links`.

- [ ] **Step 3: Add parser functions**

Append to `app/services/managed_user_service.py`:

```python

def _header_indexes(header: list[str]) -> dict[str, int] | None:
    try:
        return {name: header.index(name) for name in MANAGED_USER_TEMPLATE_HEADERS}
    except ValueError:
        return None


def _parse_bool(value: str, default: bool) -> bool | None:
    if not value:
        return default
    return ENABLED_LABELS.get(value.lower()) if value.lower() in ENABLED_LABELS else ENABLED_LABELS.get(value)


def parse_managed_user_excel(raw: bytes) -> ManagedUserParseResult:
    wb = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    ws = wb.active
    header = [_cell_text(c.value) for c in next(ws.iter_rows(min_row=1, max_row=1))]
    indexes = _header_indexes(header)
    if indexes is None:
        return ManagedUserParseResult(rows=[], errors=[{"row": 1, "reason": "表头必须包含工号、姓名、邮箱、一级部门、主角色、兼任教练、所属教练工号、启用状态"}])

    latest: dict[str, dict[str, Any]] = {}
    row_numbers: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    for row_no, row in enumerate(ws.iter_rows(min_row=2), start=2):
        if row_no - 1 > MAX_WHITELIST_ROWS:
            errors.append({"row": row_no, "reason": f"超过最大导入行数 {MAX_WHITELIST_ROWS}"})
            break
        employee_no = normalize_employee_no(_cell_text(row[indexes["工号"]].value if indexes["工号"] < len(row) else None))
        if not employee_no:
            errors.append({"row": row_no, "reason": "工号为空"})
            continue
        role_text = _cell_text(row[indexes["主角色"]].value if indexes["主角色"] < len(row) else None) or "学员"
        coach_flag_text = _cell_text(row[indexes["兼任教练"]].value if indexes["兼任教练"] < len(row) else None)
        coach_flag = _parse_bool(coach_flag_text, False)
        if coach_flag is None:
            errors.append({"row": row_no, "reason": "兼任教练必须是是或否"})
            continue
        try:
            normalized = normalize_managed_user_role(role_text, coach_flag, None)
        except ValueError as exc:
            errors.append({"row": row_no, "reason": str(exc)})
            continue
        if normalized["primary_role"] == "student" and coach_flag:
            errors.append({"row": row_no, "reason": "学员不能兼任教练"})
            continue
        enabled_text = _cell_text(row[indexes["启用状态"]].value if indexes["启用状态"] < len(row) else None)
        enabled = _parse_bool(enabled_text, True)
        if enabled is None:
            errors.append({"row": row_no, "reason": "启用状态必须是启用或禁用"})
            continue
        latest[employee_no] = {
            "employee_no": employee_no,
            "name": _cell_text(row[indexes["姓名"]].value if indexes["姓名"] < len(row) else None) or None,
            "email": _cell_text(row[indexes["邮箱"]].value if indexes["邮箱"] < len(row) else None) or None,
            "department_level1": _cell_text(row[indexes["一级部门"]].value if indexes["一级部门"] < len(row) else None) or None,
            "primary_role": normalized["primary_role"],
            "is_coach": normalized["is_coach"],
            "coach_employee_no": _cell_text(row[indexes["所属教练工号"]].value if indexes["所属教练工号"] < len(row) else None) or None,
            "enabled": enabled,
        }
        row_numbers[employee_no] = row_no
    rows = []
    for employee_no, payload in latest.items():
        payload["_row"] = row_numbers[employee_no]
        rows.append(payload)
    for payload in rows:
        payload.pop("_row")
    return ManagedUserParseResult(rows=rows, errors=errors)


def resolve_import_coach_links(rows: list[dict[str, Any]], existing_coach_employee_nos: set[str]) -> list[dict[str, Any]]:
    batch_coaches = {row["employee_no"] for row in rows if is_effective_coach(row["primary_role"], row["is_coach"])}
    valid_coaches = existing_coach_employee_nos | batch_coaches
    errors: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=2):
        coach_employee_no = row.get("coach_employee_no")
        if row["primary_role"] == "student" and coach_employee_no and coach_employee_no not in valid_coaches:
            errors.append({"row": idx, "reason": "所属教练工号不存在或不是教练"})
    return errors
```

- [ ] **Step 4: Run tests and fix duplicate import if needed**

Run:

```bash
uv run pytest tests/unit/test_managed_user_service.py -q
```

Expected: PASS.

If Python reports duplicate imports only, keep a single `from io import BytesIO` and a single `from openpyxl import Workbook, load_workbook` at the top of the test file.

- [ ] **Step 5: Commit**

```bash
git add app/services/managed_user_service.py tests/unit/test_managed_user_service.py
git commit -m "feat: parse managed user imports"
```

---

### Task 3: Add managed user database operations and CAS access gate

**Files:**
- Modify: `app/services/managed_user_service.py`
- Modify: `app/services/user_service.py:47-80`
- Modify: `app/api/v1/routes/cas.py:20-78`
- Test: `tests/unit/test_managed_user_service.py`
- Test: `tests/unit/test_user_service_admin.py`

- [ ] **Step 1: Add failing service tests for access and upsert behavior**

Append to `tests/unit/test_managed_user_service.py`:

```python
import pytest

from app.models.db_models import ManagedUserDB
from app.services.managed_user_service import ensure_managed_user_allowed, upsert_managed_user


class FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value if isinstance(self.value, list) else []


class FakeDb:
    def __init__(self, execute_values=None):
        self.execute_values = list(execute_values or [])
        self.added = []
        self.committed = False
        self.refreshed = []

    async def execute(self, stmt):
        value = self.execute_values.pop(0) if self.execute_values else None
        return FakeScalarResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        self.refreshed.append(obj)


@pytest.mark.asyncio
async def test_ensure_managed_user_allowed_rejects_missing_non_system_admin():
    db = FakeDb([None])
    with pytest.raises(PermissionError) as exc:
        await ensure_managed_user_allowed(db, "1001", False)
    assert str(exc.value) == "当前账号未开通岗标 AI 教练访问权限，请联系管理员开通。"


@pytest.mark.asyncio
async def test_ensure_managed_user_allowed_allows_enabled_profile():
    profile = ManagedUserDB(employee_no="1001", enabled=True, primary_role="student", is_coach=False)
    db = FakeDb([profile])
    assert await ensure_managed_user_allowed(db, "1001", False) is profile


@pytest.mark.asyncio
async def test_ensure_managed_user_allowed_creates_missing_system_admin_profile():
    db = FakeDb([None])
    profile = await ensure_managed_user_allowed(db, "9999", True)
    assert profile.employee_no == "9999"
    assert profile.primary_role == "admin"
    assert profile.enabled is True
    assert profile in db.added
    assert db.committed is True


@pytest.mark.asyncio
async def test_upsert_managed_user_updates_existing_profile_without_disabling_system_admin():
    profile = ManagedUserDB(employee_no="9999", enabled=True, primary_role="admin", is_coach=False)
    db = FakeDb([profile])
    updated, created = await upsert_managed_user(
        db,
        {
            "employee_no": "9999",
            "name": "系统管理员",
            "email": "admin@example.com",
            "department_level1": "总部",
            "primary_role": "student",
            "is_coach": False,
            "coach_id": None,
            "enabled": False,
        },
        source="manual",
        created_by=None,
        admin_employee_nos={"9999"},
    )
    assert created is False
    assert updated.primary_role == "admin"
    assert updated.enabled is True
    assert updated.name == "系统管理员"
```

Create or append `tests/unit/test_user_service_admin.py`:

```python
import pytest

from app.models.db_models import ManagedUserDB, User
from app.services.user_service import upsert_sso_user
from tests.unit.test_managed_user_service import FakeDb, FakeScalarResult


@pytest.mark.asyncio
async def test_upsert_sso_user_links_managed_profile_on_create():
    profile = ManagedUserDB(employee_no="1001", email="a@example.com", name="张三")
    db = FakeDb([None, None])
    user = await upsert_sso_user(db, "1001", {"RJEMAIL": "a@example.com", "RJXM": "张三"}, managed_user=profile)
    assert user.managed_user is profile
    assert user.managed_user_id == profile.id


@pytest.mark.asyncio
async def test_upsert_sso_user_links_managed_profile_on_existing_user():
    profile = ManagedUserDB(employee_no="1001", email="a@example.com", name="张三")
    user = User(provider="cas", provider_user_id="1001", email="old@example.com", nickname="Old", password_hash=None)
    db = FakeDb([user, None])
    updated = await upsert_sso_user(db, "1001", {"RJEMAIL": "a@example.com", "RJXM": "张三"}, managed_user=profile)
    assert updated is user
    assert updated.managed_user is profile
    assert updated.managed_user_id == profile.id
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/unit/test_managed_user_service.py tests/unit/test_user_service_admin.py -q
```

Expected: FAIL because `ensure_managed_user_allowed`, `upsert_managed_user`, or `managed_user` parameter is missing.

- [ ] **Step 3: Implement managed user DB operations**

Append to `app/services/managed_user_service.py`:

```python
async def get_managed_user_by_employee_no(db: AsyncSession, employee_no: str) -> ManagedUserDB | None:
    result = await db.execute(select(ManagedUserDB).where(ManagedUserDB.employee_no == employee_no))
    return result.scalar_one_or_none()


async def ensure_managed_user_allowed(db: AsyncSession, employee_no: str, is_system_admin: bool) -> ManagedUserDB:
    profile = await get_managed_user_by_employee_no(db, employee_no)
    if profile is None and is_system_admin:
        profile = ManagedUserDB(
            employee_no=employee_no,
            primary_role="admin",
            is_coach=False,
            enabled=True,
            source="system",
        )
        db.add(profile)
        await db.commit()
        await db.refresh(profile)
        return profile
    if profile is None:
        raise PermissionError(WHITELIST_DENY_MESSAGE)
    if not profile.enabled and not is_system_admin:
        raise PermissionError(WHITELIST_DENY_MESSAGE)
    if is_system_admin and (profile.primary_role != "admin" or not profile.enabled):
        profile.primary_role = "admin"
        profile.enabled = True
        await db.commit()
        await db.refresh(profile)
    return profile


async def existing_coach_employee_nos(db: AsyncSession) -> set[str]:
    result = await db.execute(select(ManagedUserDB).where(ManagedUserDB.primary_role.in_(["admin", "coach"])))
    profiles = result.scalars().all()
    return {p.employee_no for p in profiles if is_effective_coach(p.primary_role, p.is_coach)}


async def upsert_managed_user(
    db: AsyncSession,
    payload: dict[str, Any],
    source: str,
    created_by: uuid.UUID | None,
    admin_employee_nos: set[str],
) -> tuple[ManagedUserDB, bool]:
    employee_no = normalize_employee_no(payload["employee_no"])
    requested = protect_system_admin_patch(employee_no, admin_employee_nos, dict(payload))
    normalized = normalize_managed_user_role(requested.get("primary_role"), requested.get("is_coach", False), requested.get("coach_id"))
    requested.update(normalized)
    result = await db.execute(select(ManagedUserDB).where(ManagedUserDB.employee_no == employee_no))
    profile = result.scalar_one_or_none()
    created = profile is None
    if profile is None:
        profile = ManagedUserDB(employee_no=employee_no, created_by=created_by)
        db.add(profile)
    profile.name = requested.get("name")
    profile.email = requested.get("email")
    profile.department_level1 = requested.get("department_level1")
    profile.primary_role = requested["primary_role"]
    profile.is_coach = requested["is_coach"]
    profile.coach_id = requested.get("coach_id") if requested["primary_role"] == "student" else None
    profile.enabled = requested.get("enabled", True)
    profile.source = source
    return profile, created
```

- [ ] **Step 4: Modify SSO user upsert to link profiles**

In `app/services/user_service.py`, change signature:

```python
async def upsert_sso_user(db: AsyncSession, employee_no: str, attrs: dict, is_admin: bool = False, managed_user=None) -> User:
```

Inside the new-user `User(...)` constructor, add:

```python
            managed_user=managed_user,
```

Inside the existing-user branch before admin promotion, add:

```python
        if managed_user is not None:
            user.managed_user = managed_user
            user.managed_user_id = managed_user.id
```

After creating a new user, ensure in-memory tests see the profile id:

```python
        if managed_user is not None:
            user.managed_user_id = managed_user.id
```

- [ ] **Step 5: Change CAS gate to managed users**

In `app/api/v1/routes/cas.py`, replace whitelist imports:

```python
from app.services.managed_user_service import WHITELIST_DENY_MESSAGE, ensure_managed_user_allowed, normalize_employee_no
```

Replace `ensure_sso_allowed` with:

```python
async def ensure_sso_allowed(
    db: AsyncSession,
    employee_no: str,
    is_admin_employee_no: bool,
):
    try:
        return await ensure_managed_user_allowed(db, employee_no, is_admin_employee_no)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
```

In `cas_exchange`, replace:

```python
    await ensure_sso_allowed(db, employee_no, is_admin_employee_no)
```

with:

```python
    managed_user = await ensure_sso_allowed(db, employee_no, is_admin_employee_no)
```

And replace SSO upsert call with:

```python
    user = await upsert_sso_user(db, employee_no, attrs, is_admin=is_admin_employee_no, managed_user=managed_user)
```

- [ ] **Step 6: Run targeted tests**

Run:

```bash
uv run pytest tests/unit/test_managed_user_service.py tests/unit/test_user_service_admin.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/managed_user_service.py app/services/user_service.py app/api/v1/routes/cas.py tests/unit/test_managed_user_service.py tests/unit/test_user_service_admin.py
git commit -m "feat: gate SSO with managed users"
```

---

### Task 4: Add admin users API

**Files:**
- Modify: `app/api/v1/routes/admin.py`
- Modify: `app/models/schema.py`
- Test: `tests/integration/test_admin_users_api.py`
- Modify: `tests/integration/test_admin_whitelist_api.py`

- [ ] **Step 1: Write failing API tests**

Create `tests/integration/test_admin_users_api.py`:

```python
import uuid

import main
from app.api.deps import get_current_user, get_db
from app.models.db_models import ManagedUserDB, User


def _user(is_admin: bool):
    u = User()
    u.id = uuid.uuid4()
    u.email = "admin@example.com"
    u.nickname = "Admin"
    u.is_active = True
    u.is_admin = is_admin
    return u


class FakeScalarList:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None


class FakeAdminDb:
    def __init__(self):
        self.profile = ManagedUserDB(
            id=uuid.uuid4(),
            employee_no="1001",
            name="张三",
            email="a@example.com",
            department_level1="研发",
            primary_role="student",
            is_coach=False,
            enabled=True,
            source="manual",
        )
        self.added = []

    async def execute(self, stmt):
        return FakeScalarList([self.profile])

    async def get(self, model, obj_id):
        return self.profile if str(obj_id) == str(self.profile.id) else None

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()



def test_admin_users_requires_admin(client):
    main.app.dependency_overrides[get_current_user] = lambda: _user(False)
    resp = client.get("/api/v1/admin/users")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "admin_required"


def test_admin_users_list_returns_managed_profiles(client):
    db = FakeAdminDb()
    main.app.dependency_overrides[get_current_user] = lambda: _user(True)
    main.app.dependency_overrides[get_db] = lambda: db
    resp = client.get("/api/v1/admin/users")
    assert resp.status_code == 200
    assert resp.json()[0]["employee_no"] == "1001"
    assert resp.json()[0]["department_level1"] == "研发"


def test_admin_users_template_download(client):
    main.app.dependency_overrides[get_current_user] = lambda: _user(True)
    resp = client.get("/api/v1/admin/users/template")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def test_admin_users_coaches_returns_only_effective_coaches(client):
    coach = ManagedUserDB(id=uuid.uuid4(), employee_no="2001", name="教练", primary_role="coach", is_coach=True, enabled=True)
    admin_coach = ManagedUserDB(id=uuid.uuid4(), employee_no="3001", name="管理员教练", primary_role="admin", is_coach=True, enabled=True)
    student = ManagedUserDB(id=uuid.uuid4(), employee_no="1001", name="学员", primary_role="student", is_coach=False, enabled=True)

    class CoachDb(FakeAdminDb):
        async def execute(self, stmt):
            return FakeScalarList([coach, admin_coach, student])

    main.app.dependency_overrides[get_current_user] = lambda: _user(True)
    main.app.dependency_overrides[get_db] = lambda: CoachDb()
    resp = client.get("/api/v1/admin/users/coaches")
    assert resp.status_code == 200
    assert [row["employee_no"] for row in resp.json()] == ["2001", "3001"]
```

- [ ] **Step 2: Run tests and verify missing route failure**

Run:

```bash
uv run pytest tests/integration/test_admin_users_api.py -q
```

Expected: FAIL with 404 for `/api/v1/admin/users` or import errors for new response helpers.

- [ ] **Step 3: Add admin route request models and row serializer**

In `app/api/v1/routes/admin.py`, extend imports:

```python
from app.core.config import get_admin_employee_no_set
from app.models.db_models import ManagedUserDB, SsoUserWhitelistDB, User
from app.services.managed_user_service import (
    MAX_WHITELIST_UPLOAD_BYTES,
    build_managed_user_template,
    existing_coach_employee_nos,
    is_effective_coach,
    parse_managed_user_excel,
    resolve_import_coach_links,
    upsert_managed_user,
)
```

Add models after existing whitelist request models:

```python
class ManagedUserRequest(BaseModel):
    employee_no: str
    name: str | None = None
    email: str | None = None
    department_level1: str | None = None
    primary_role: str = "student"
    is_coach: bool = False
    coach_id: uuid.UUID | None = None
    enabled: bool = True


class ManagedUserPatchRequest(BaseModel):
    name: str | None = None
    email: str | None = None
    department_level1: str | None = None
    primary_role: str | None = None
    is_coach: bool | None = None
    coach_id: uuid.UUID | None = None
    enabled: bool | None = None
```

Add serializers:

```python
def _managed_user_row(e: ManagedUserDB) -> dict:
    return {
        "id": str(e.id),
        "employee_no": e.employee_no,
        "name": e.name,
        "email": e.email,
        "department_level1": e.department_level1,
        "primary_role": e.primary_role,
        "is_coach": e.is_coach,
        "coach_id": str(e.coach_id) if e.coach_id else None,
        "coach_name": e.coach.name if getattr(e, "coach", None) else None,
        "enabled": e.enabled,
        "source": e.source,
        "is_system_admin": e.employee_no in get_admin_employee_no_set(),
        "created_at": e.created_at.isoformat() if e.created_at else "",
        "updated_at": e.updated_at.isoformat() if e.updated_at else "",
    }


def _coach_row(e: ManagedUserDB) -> dict:
    return {"id": str(e.id), "employee_no": e.employee_no, "name": e.name, "department_level1": e.department_level1}
```

- [ ] **Step 4: Add users endpoints**

Append to `app/api/v1/routes/admin.py` before old whitelist endpoints or after them:

```python
@router.get("/users")
async def list_managed_users(
    q: str | None = None,
    role: str | None = None,
    enabled: bool | None = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    stmt = select(ManagedUserDB).order_by(ManagedUserDB.updated_at.desc())
    if role:
        stmt = stmt.where(ManagedUserDB.primary_role == role)
    if enabled is not None:
        stmt = stmt.where(ManagedUserDB.enabled == enabled)
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    if q:
        needle = q.lower().strip()
        rows = [
            row for row in rows
            if needle in row.employee_no.lower()
            or (row.name and needle in row.name.lower())
            or (row.email and needle in row.email.lower())
        ]
    return [_managed_user_row(row) for row in rows]


@router.post("/users")
async def create_managed_user(
    body: ManagedUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    profile, _created = await upsert_managed_user(
        db,
        body.model_dump(),
        source="manual",
        created_by=admin.id,
        admin_employee_nos=get_admin_employee_no_set(),
    )
    await db.commit()
    await db.refresh(profile)
    return _managed_user_row(profile)


@router.patch("/users/{profile_id}")
async def patch_managed_user(
    profile_id: uuid.UUID,
    body: ManagedUserPatchRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    profile = await db.get(ManagedUserDB, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="not_found")
    payload = _managed_user_row(profile)
    payload.update({k: v for k, v in body.model_dump().items() if v is not None})
    payload["employee_no"] = profile.employee_no
    updated, _created = await upsert_managed_user(
        db,
        payload,
        source="manual",
        created_by=admin.id,
        admin_employee_nos=get_admin_employee_no_set(),
    )
    await db.commit()
    await db.refresh(updated)
    return _managed_user_row(updated)


@router.get("/users/template")
async def managed_users_template(admin: User = Depends(require_admin)):
    return StreamingResponse(
        BytesIO(build_managed_user_template()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=managed-users-template.xlsx"},
    )


@router.get("/users/coaches")
async def list_coaches(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await db.execute(select(ManagedUserDB).where(ManagedUserDB.enabled == True).order_by(ManagedUserDB.employee_no))
    rows = [row for row in result.scalars().all() if is_effective_coach(row.primary_role, row.is_coach)]
    return [_coach_row(row) for row in rows]
```

- [ ] **Step 5: Add import endpoint**

Append to `app/api/v1/routes/admin.py`:

```python
@router.post("/users/import")
async def import_managed_users(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")
    raw = await file.read(MAX_WHITELIST_UPLOAD_BYTES + 1)
    if len(raw) > MAX_WHITELIST_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件过大")
    parsed = parse_managed_user_excel(raw)
    coach_errors = resolve_import_coach_links(parsed.rows, await existing_coach_employee_nos(db))
    if coach_errors:
        parsed.errors.extend(coach_errors)
    created = updated = 0
    for row in parsed.rows:
        if row.get("coach_employee_no"):
            coach_result = await db.execute(select(ManagedUserDB).where(ManagedUserDB.employee_no == row["coach_employee_no"]))
            coach = coach_result.scalar_one_or_none()
            row["coach_id"] = coach.id if coach else None
        row.pop("coach_employee_no", None)
        profile, was_created = await upsert_managed_user(
            db,
            row,
            source="excel",
            created_by=admin.id,
            admin_employee_nos=get_admin_employee_no_set(),
        )
        created += 1 if was_created else 0
        updated += 0 if was_created else 1
    await db.commit()
    return {"created": created, "updated": updated, "skipped": len(parsed.errors), "errors": parsed.errors}
```

- [ ] **Step 6: Update auth response schema with managed role fields**

In `app/models/schema.py`, extend `UserResponse`:

```python
class UserResponse(BaseModel):
    id: str
    email: str | None
    nickname: str | None
    is_active: bool
    is_admin: bool
    managed_user_id: str | None = None
    employee_no: str | None = None
    primary_role: str | None = None
    is_coach: bool = False
    created_at: datetime
```

In `app/api/v1/routes/auth.py`, update `me` return:

```python
    managed = current_user.managed_user
    return UserResponse(
        id=str(current_user.id),
        email=current_user.email,
        nickname=current_user.nickname,
        is_active=current_user.is_active,
        is_admin=current_user.is_admin or (managed.primary_role == "admin" if managed else False),
        managed_user_id=str(managed.id) if managed else None,
        employee_no=managed.employee_no if managed else current_user.provider_user_id,
        primary_role=managed.primary_role if managed else None,
        is_coach=(managed.primary_role == "coach" or (managed.primary_role == "admin" and managed.is_coach)) if managed else False,
        created_at=current_user.created_at,
    )
```

- [ ] **Step 7: Run targeted API tests**

Run:

```bash
uv run pytest tests/integration/test_admin_users_api.py tests/integration/test_admin_whitelist_api.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/api/v1/routes/admin.py app/models/schema.py app/api/v1/routes/auth.py tests/integration/test_admin_users_api.py tests/integration/test_admin_whitelist_api.py
git commit -m "feat: add admin managed user api"
```

---

### Task 5: Add admin conversation service and API

**Files:**
- Create: `app/services/admin_conversation_service.py`
- Modify: `app/api/v1/routes/admin.py`
- Test: `tests/unit/test_admin_conversation_service.py`
- Test: `tests/integration/test_admin_conversations_api.py`

- [ ] **Step 1: Write failing unit tests for visibility helpers**

Create `tests/unit/test_admin_conversation_service.py`:

```python
import uuid

from app.models.db_models import ManagedUserDB, User
from app.services.admin_conversation_service import can_view_student, default_conversation_scope, is_admin_user, is_coach_user


def _login_user(profile):
    user = User()
    user.id = uuid.uuid4()
    user.is_admin = False
    user.managed_user = profile
    user.managed_user_id = profile.id if profile else None
    return user


def test_default_scope_for_admin_coach_is_mine():
    profile = ManagedUserDB(id=uuid.uuid4(), primary_role="admin", is_coach=True)
    assert default_conversation_scope(_login_user(profile)) == "mine"


def test_default_scope_for_plain_admin_is_all():
    profile = ManagedUserDB(id=uuid.uuid4(), primary_role="admin", is_coach=False)
    assert default_conversation_scope(_login_user(profile)) == "all"


def test_is_coach_user_accepts_coach_and_admin_coach():
    assert is_coach_user(_login_user(ManagedUserDB(primary_role="coach", is_coach=True))) is True
    assert is_coach_user(_login_user(ManagedUserDB(primary_role="admin", is_coach=True))) is True
    assert is_coach_user(_login_user(ManagedUserDB(primary_role="admin", is_coach=False))) is False


def test_can_view_student_for_coach_requires_assignment():
    coach_id = uuid.uuid4()
    coach = _login_user(ManagedUserDB(id=coach_id, primary_role="coach", is_coach=True))
    assigned = ManagedUserDB(id=uuid.uuid4(), primary_role="student", coach_id=coach_id)
    other = ManagedUserDB(id=uuid.uuid4(), primary_role="student", coach_id=uuid.uuid4())
    assert can_view_student(coach, assigned, "mine") is True
    assert can_view_student(coach, other, "mine") is False


def test_admin_can_view_all_students():
    admin = _login_user(ManagedUserDB(id=uuid.uuid4(), primary_role="admin", is_coach=False))
    student = ManagedUserDB(id=uuid.uuid4(), primary_role="student", coach_id=None)
    assert can_view_student(admin, student, "all") is True
```

- [ ] **Step 2: Run tests and verify missing service failure**

Run:

```bash
uv run pytest tests/unit/test_admin_conversation_service.py -q
```

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement visibility helpers and serializers**

Create `app/services/admin_conversation_service.py`:

```python
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.db_models import ChatMessageDB, ChatSessionDB, ManagedUserDB, User
from app.services.managed_user_service import is_effective_coach
from app.services.session_service import db_session_history_for_client, db_session_summary_for_client


def is_admin_user(user: User) -> bool:
    managed = getattr(user, "managed_user", None)
    return bool(user.is_admin or (managed and managed.primary_role == "admin"))


def is_coach_user(user: User) -> bool:
    managed = getattr(user, "managed_user", None)
    return bool(managed and is_effective_coach(managed.primary_role, managed.is_coach))


def default_conversation_scope(user: User) -> str:
    return "mine" if is_admin_user(user) and is_coach_user(user) else "all"


def can_view_student(user: User, student: ManagedUserDB, scope: str) -> bool:
    if is_admin_user(user) and scope == "all":
        return True
    managed = getattr(user, "managed_user", None)
    return bool(managed and is_coach_user(user) and student.coach_id == managed.id)


def _require_conversation_access(user: User, student: ManagedUserDB, scope: str) -> None:
    if not can_view_student(user, student, scope):
        raise HTTPException(status_code=403, detail="conversation_forbidden")


async def list_conversation_students(db: AsyncSession, user: User, scope: str) -> list[dict[str, Any]]:
    if scope == "all" and not is_admin_user(user):
        raise HTTPException(status_code=403, detail="conversation_forbidden")
    stmt = select(ManagedUserDB).where(ManagedUserDB.primary_role == "student").order_by(ManagedUserDB.employee_no)
    result = await db.execute(stmt)
    students = [s for s in result.scalars().all() if can_view_student(user, s, scope)]
    rows = []
    for student in students:
        session_result = await db.execute(
            select(ChatSessionDB).join(User, ChatSessionDB.user_id == User.id).where(User.managed_user_id == student.id)
        )
        sessions = list(session_result.scalars().all())
        rows.append({
            "managed_user_id": str(student.id),
            "employee_no": student.employee_no,
            "name": student.name,
            "department_level1": student.department_level1,
            "coach_id": str(student.coach_id) if student.coach_id else None,
            "session_count": len(sessions),
            "latest_session_at": max((s.updated_at for s in sessions if s.updated_at), default=None).isoformat() if sessions else None,
        })
    return rows


async def list_student_sessions(db: AsyncSession, user: User, managed_user_id: uuid.UUID) -> list[dict[str, Any]]:
    student = await db.get(ManagedUserDB, managed_user_id)
    if student is None:
        raise HTTPException(status_code=404, detail="student_not_found")
    _require_conversation_access(user, student, "all" if is_admin_user(user) else "mine")
    result = await db.execute(
        select(ChatSessionDB)
        .options(selectinload(ChatSessionDB.messages))
        .join(User, ChatSessionDB.user_id == User.id)
        .where(User.managed_user_id == managed_user_id)
        .order_by(ChatSessionDB.updated_at.desc())
    )
    sessions = list(result.scalars().all())
    rows = []
    for session in sessions:
        summary = db_session_summary_for_client(session)
        summary["message_count"] = len([m for m in session.messages if m.visible_in_history])
        rows.append(summary)
    return rows


async def get_conversation_session(db: AsyncSession, user: User, session_id: uuid.UUID) -> dict[str, Any]:
    result = await db.execute(
        select(ChatSessionDB)
        .options(selectinload(ChatSessionDB.messages), selectinload(ChatSessionDB.user).selectinload(User.managed_user))
        .where(ChatSessionDB.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="session_not_found")
    student = session.user.managed_user
    if student is None:
        raise HTTPException(status_code=404, detail="student_not_found")
    _require_conversation_access(user, student, "all" if is_admin_user(user) else "mine")
    return {
        "session_id": str(session.id),
        "student": {
            "managed_user_id": str(student.id),
            "employee_no": student.employee_no,
            "name": student.name,
            "department_level1": student.department_level1,
        },
        "created_at": session.created_at.isoformat() if session.created_at else "",
        "updated_at": session.updated_at.isoformat() if session.updated_at else "",
        "history": db_session_history_for_client(session),
    }
```

- [ ] **Step 4: Add API endpoints**

In `app/api/v1/routes/admin.py`, import:

```python
from app.services.admin_conversation_service import get_conversation_session, list_conversation_students, list_student_sessions
```

Append endpoints:

```python
@router.get("/conversations/users")
async def admin_conversation_users(
    scope: str = "all",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await list_conversation_students(db, current_user, scope)


@router.get("/conversations/users/{managed_user_id}/sessions")
async def admin_conversation_user_sessions(
    managed_user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await list_student_sessions(db, current_user, managed_user_id)


@router.get("/conversations/sessions/{session_id}")
async def admin_conversation_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await get_conversation_session(db, current_user, session_id)
```

- [ ] **Step 5: Add API smoke tests using monkeypatch seams**

Create `tests/integration/test_admin_conversations_api.py`:

```python
import uuid

import main
from app.api.deps import get_current_user, get_db
from app.models.db_models import ManagedUserDB, User


def _coach_user():
    profile = ManagedUserDB(id=uuid.uuid4(), primary_role="coach", is_coach=True)
    user = User()
    user.id = uuid.uuid4()
    user.email = "coach@example.com"
    user.is_active = True
    user.is_admin = False
    user.managed_user = profile
    user.managed_user_id = profile.id
    return user


def test_conversation_users_route_returns_service_payload(client, monkeypatch):
    async def fake_list(db, user, scope):
        return [{"managed_user_id": "student-id", "employee_no": "1001", "session_count": 2}]

    monkeypatch.setattr("app.api.v1.routes.admin.list_conversation_students", fake_list)
    main.app.dependency_overrides[get_current_user] = _coach_user
    main.app.dependency_overrides[get_db] = lambda: object()
    resp = client.get("/api/v1/admin/conversations/users?scope=mine")
    assert resp.status_code == 200
    assert resp.json() == [{"managed_user_id": "student-id", "employee_no": "1001", "session_count": 2}]


def test_conversation_session_route_returns_readonly_detail(client, monkeypatch):
    session_id = uuid.uuid4()

    async def fake_detail(db, user, target_session_id):
        assert target_session_id == session_id
        return {"session_id": str(session_id), "history": [{"role": "user", "content": "hello"}]}

    monkeypatch.setattr("app.api.v1.routes.admin.get_conversation_session", fake_detail)
    main.app.dependency_overrides[get_current_user] = _coach_user
    main.app.dependency_overrides[get_db] = lambda: object()
    resp = client.get(f"/api/v1/admin/conversations/sessions/{session_id}")
    assert resp.status_code == 200
    assert resp.json()["history"] == [{"role": "user", "content": "hello"}]
```

- [ ] **Step 6: Run targeted tests**

Run:

```bash
uv run pytest tests/unit/test_admin_conversation_service.py tests/integration/test_admin_conversations_api.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/services/admin_conversation_service.py app/api/v1/routes/admin.py tests/unit/test_admin_conversation_service.py tests/integration/test_admin_conversations_api.py
git commit -m "feat: add admin conversation history api"
```

---

### Task 6: Add frontend admin types and API client

**Files:**
- Modify: `frontend/types/auth.ts`
- Modify: `frontend/types/admin.ts`
- Modify: `frontend/lib/admin.ts`
- Modify: `frontend/app/page.tsx:280-285`

- [ ] **Step 1: Update auth user type**

In `frontend/types/auth.ts`, replace `UserInfo` with:

```ts
export interface UserInfo {
  id: string;
  email: string | null;
  nickname: string | null;
  is_active: boolean;
  is_admin: boolean;
  managed_user_id: string | null;
  employee_no: string | null;
  primary_role: "admin" | "coach" | "student" | null;
  is_coach: boolean;
  created_at: string;
}
```

- [ ] **Step 2: Replace admin types**

In `frontend/types/admin.ts`, replace contents with:

```ts
import type { ChatHistoryItem } from "./chat";

export type ManagedUserRole = "admin" | "coach" | "student";

export interface ManagedUser {
  id: string;
  employee_no: string;
  name: string | null;
  email: string | null;
  department_level1: string | null;
  primary_role: ManagedUserRole;
  is_coach: boolean;
  coach_id: string | null;
  coach_name: string | null;
  enabled: boolean;
  source: string;
  is_system_admin: boolean;
  created_at: string;
  updated_at: string;
}

export interface ManagedUserPayload {
  employee_no: string;
  name?: string | null;
  email?: string | null;
  department_level1?: string | null;
  primary_role: ManagedUserRole;
  is_coach: boolean;
  coach_id?: string | null;
  enabled: boolean;
}

export interface CoachOption {
  id: string;
  employee_no: string;
  name: string | null;
  department_level1: string | null;
}

export interface ImportResult {
  created: number;
  updated: number;
  skipped: number;
  errors: { row: number; reason: string }[];
}

export interface ConversationUserSummary {
  managed_user_id: string;
  employee_no: string;
  name: string | null;
  department_level1: string | null;
  coach_id: string | null;
  session_count: number;
  latest_session_at: string | null;
}

export interface AdminSessionSummary {
  session_id: string;
  created_at: string;
  updated_at: string;
  latest_preview: string;
  message_count: number;
}

export interface AdminConversationDetail {
  session_id: string;
  student: {
    managed_user_id: string;
    employee_no: string;
    name: string | null;
    department_level1: string | null;
  };
  created_at: string;
  updated_at: string;
  history: ChatHistoryItem[];
}
```

- [ ] **Step 3: Replace admin API client**

In `frontend/lib/admin.ts`, replace contents with:

```ts
import { getCsrfToken } from "./auth";
import type {
  AdminConversationDetail,
  AdminSessionSummary,
  CoachOption,
  ConversationUserSummary,
  ImportResult,
  ManagedUser,
  ManagedUserPayload,
} from "@/types/admin";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "";
const endpoint = (path: string) => `${API_BASE}${path}`;

function headers(json = true): HeadersInit {
  const h: Record<string, string> = {};
  const csrf = getCsrfToken();
  if (csrf) h["X-CSRF-Token"] = csrf;
  if (json) h["Content-Type"] = "application/json";
  return h;
}

async function adminFetch(path: string, options: RequestInit = {}) {
  const resp = await fetch(endpoint(path), { ...options, credentials: "include" });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.detail || `请求失败: ${resp.status}`);
  }
  return resp;
}

export async function listManagedUsers(params: { q?: string; role?: string; enabled?: string } = {}): Promise<ManagedUser[]> {
  const query = new URLSearchParams();
  if (params.q) query.set("q", params.q);
  if (params.role) query.set("role", params.role);
  if (params.enabled) query.set("enabled", params.enabled);
  const suffix = query.toString() ? `?${query.toString()}` : "";
  return (await adminFetch(`/api/v1/admin/users${suffix}`, { cache: "no-store" })).json();
}

export async function createManagedUser(payload: ManagedUserPayload): Promise<ManagedUser> {
  return (await adminFetch("/api/v1/admin/users", {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(payload),
  })).json();
}

export async function updateManagedUser(id: string, payload: Partial<ManagedUserPayload>): Promise<ManagedUser> {
  return (await adminFetch(`/api/v1/admin/users/${id}`, {
    method: "PATCH",
    headers: headers(),
    body: JSON.stringify(payload),
  })).json();
}

export async function listCoachOptions(): Promise<CoachOption[]> {
  return (await adminFetch("/api/v1/admin/users/coaches", { cache: "no-store" })).json();
}

export async function importManagedUsers(file: File): Promise<ImportResult> {
  const form = new FormData();
  form.append("file", file);
  return (await adminFetch("/api/v1/admin/users/import", {
    method: "POST",
    headers: headers(false),
    body: form,
  })).json();
}

export function managedUsersTemplateUrl(): string {
  return endpoint("/api/v1/admin/users/template");
}

export async function listConversationUsers(scope: "mine" | "all"): Promise<ConversationUserSummary[]> {
  return (await adminFetch(`/api/v1/admin/conversations/users?scope=${scope}`, { cache: "no-store" })).json();
}

export async function listConversationSessions(managedUserId: string): Promise<AdminSessionSummary[]> {
  return (await adminFetch(`/api/v1/admin/conversations/users/${managedUserId}/sessions`, { cache: "no-store" })).json();
}

export async function getConversationSession(sessionId: string): Promise<AdminConversationDetail> {
  return (await adminFetch(`/api/v1/admin/conversations/sessions/${sessionId}`, { cache: "no-store" })).json();
}
```

- [ ] **Step 4: Point chat user menu to admin root**

In `frontend/app/page.tsx`, replace the admin menu button destination:

```tsx
<button type="button" onClick={() => { window.location.href = "/admin"; }}>
  管理后台
</button>
```

- [ ] **Step 5: Run frontend build and verify type failures are limited to missing pages if any**

Run:

```bash
npm --prefix frontend run build
```

Expected: It may fail because `/admin/whitelist/page.tsx` imports removed whitelist names. That is acceptable at this step and is resolved in Task 7.

- [ ] **Step 6: Commit**

```bash
git add frontend/types/auth.ts frontend/types/admin.ts frontend/lib/admin.ts frontend/app/page.tsx
git commit -m "feat: add admin frontend client types"
```

---

### Task 7: Build admin shell and users page

**Files:**
- Create: `frontend/app/admin/layout.tsx`
- Create: `frontend/app/admin/page.tsx`
- Create: `frontend/app/admin/users/page.tsx`
- Replace: `frontend/app/admin/whitelist/page.tsx`
- Modify: `frontend/app/globals.css`

- [ ] **Step 1: Create admin layout**

Create `frontend/app/admin/layout.tsx`:

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";

import { checkAuth, logout } from "@/lib/auth";
import type { UserInfo } from "@/types/auth";

const navItems = [
  { href: "/admin", label: "概览" },
  { href: "/admin/users", label: "用户管理" },
  { href: "/admin/conversations", label: "对话历史" },
];

export default function AdminLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const [user, setUser] = useState<UserInfo | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    checkAuth().then((u) => {
      setUser(u);
      setChecked(true);
    });
  }, []);

  if (!checked) {
    return <main className="admin-shell"><div className="admin-card">正在验证权限…</div></main>;
  }

  if (!user?.is_admin) {
    return (
      <main className="admin-shell admin-denied">
        <div className="admin-card">
          <h1>无权限访问</h1>
          <p>当前账号没有管理后台访问权限。</p>
          <Link className="secondary admin-link-button" href="/">返回首页</Link>
        </div>
      </main>
    );
  }

  return (
    <main className="admin-shell">
      <aside className="admin-sidebar">
        <div>
          <h1>管理后台</h1>
          <p>岗标 AI 教练</p>
        </div>
        <nav>
          {navItems.map((item) => (
            <Link key={item.href} className={pathname === item.href ? "active" : ""} href={item.href}>
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>
      <section className="admin-main">
        <header className="admin-topbar">
          <div>
            <strong>{user.nickname || user.email || "管理员"}</strong>
            <span>{user.employee_no || ""}</span>
          </div>
          <div className="admin-topbar-actions">
            <Link className="secondary admin-link-button" href="/">返回聊天首页</Link>
            <button className="secondary" type="button" onClick={() => void logout()}>退出登录</button>
          </div>
        </header>
        {children}
      </section>
    </main>
  );
}
```

- [ ] **Step 2: Create admin overview**

Create `frontend/app/admin/page.tsx`:

```tsx
export default function AdminOverviewPage() {
  return (
    <div className="admin-card">
      <h2>概览</h2>
      <p className="admin-muted">请选择左侧模块进行用户管理或查看对话历史。</p>
      <div className="admin-stat-grid">
        <div><strong>用户管理</strong><span>维护准入、角色、一级部门和教练归属</span></div>
        <div><strong>对话历史</strong><span>按权限只读查看学员会话</span></div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Replace whitelist page with redirect**

Replace `frontend/app/admin/whitelist/page.tsx`:

```tsx
import { redirect } from "next/navigation";

export default function WhitelistRedirectPage() {
  redirect("/admin/users");
}
```

- [ ] **Step 4: Create users page**

Create `frontend/app/admin/users/page.tsx`:

```tsx
"use client";

import { FormEvent, useEffect, useState } from "react";

import {
  createManagedUser,
  importManagedUsers,
  listCoachOptions,
  listManagedUsers,
  managedUsersTemplateUrl,
  updateManagedUser,
} from "@/lib/admin";
import type { CoachOption, ImportResult, ManagedUser, ManagedUserPayload, ManagedUserRole } from "@/types/admin";

const emptyForm: ManagedUserPayload = {
  employee_no: "",
  name: "",
  email: "",
  department_level1: "",
  primary_role: "student",
  is_coach: false,
  coach_id: null,
  enabled: true,
};

export default function AdminUsersPage() {
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [coaches, setCoaches] = useState<CoachOption[]>([]);
  const [editing, setEditing] = useState<ManagedUser | null>(null);
  const [form, setForm] = useState<ManagedUserPayload>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);

  useEffect(() => { void refresh(); }, []);

  async function refresh() {
    setError("");
    const [userRows, coachRows] = await Promise.all([listManagedUsers(), listCoachOptions()]);
    setUsers(userRows);
    setCoaches(coachRows);
  }

  function beginCreate() {
    setEditing(null);
    setForm(emptyForm);
  }

  function beginEdit(user: ManagedUser) {
    setEditing(user);
    setForm({
      employee_no: user.employee_no,
      name: user.name || "",
      email: user.email || "",
      department_level1: user.department_level1 || "",
      primary_role: user.primary_role,
      is_coach: user.is_coach,
      coach_id: user.coach_id,
      enabled: user.enabled,
    });
  }

  function setRole(role: ManagedUserRole) {
    setForm((prev) => ({
      ...prev,
      primary_role: role,
      is_coach: role === "coach" ? true : role === "student" ? false : prev.is_coach,
      coach_id: role === "student" ? prev.coach_id : null,
    }));
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const payload = { ...form, coach_id: form.primary_role === "student" ? form.coach_id : null };
      if (editing) {
        await updateManagedUser(editing.id, payload);
      } else {
        await createManagedUser(payload);
      }
      setEditing(null);
      setForm(emptyForm);
      await refresh();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  }

  async function onImport(file: File | null) {
    if (!file) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(await importManagedUsers(file));
      await refresh();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-stack">
      <div className="admin-card">
        <div className="admin-section-head">
          <div><h2>用户管理</h2><p>维护可使用系统的人员、角色和教练归属。</p></div>
          <button className="primary" type="button" onClick={beginCreate}>新增用户</button>
        </div>
        <div className="admin-actions">
          <a className="secondary admin-link-button" href={managedUsersTemplateUrl()}>下载模板</a>
          <label className="secondary admin-file-button">
            导入 Excel
            <input type="file" accept=".xlsx" hidden disabled={busy} onChange={(e) => void onImport(e.target.files?.[0] || null)} />
          </label>
        </div>
        {result ? <div className="admin-result">新增 {result.created}，更新 {result.updated}，跳过 {result.skipped}</div> : null}
        {error ? <div className="auth-error">{error}</div> : null}
      </div>

      <div className="admin-card admin-table-card">
        <table className="admin-table">
          <thead><tr><th>工号</th><th>姓名</th><th>邮箱</th><th>一级部门</th><th>角色</th><th>所属教练</th><th>启用</th><th>操作</th></tr></thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id}>
                <td>{user.employee_no}{user.is_system_admin ? <span className="admin-badge">系统</span> : null}</td>
                <td>{user.name || "-"}</td>
                <td>{user.email || "-"}</td>
                <td>{user.department_level1 || "-"}</td>
                <td>{roleLabel(user.primary_role)}{user.primary_role === "admin" && user.is_coach ? " / 兼任教练" : ""}</td>
                <td>{user.coach_name || "-"}</td>
                <td>{user.enabled ? "启用" : "禁用"}</td>
                <td><button className="secondary" type="button" onClick={() => beginEdit(user)}>编辑</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="admin-card">
        <h3>{editing ? "编辑用户" : "新增用户"}</h3>
        <form className="admin-form-grid" onSubmit={save}>
          <input value={form.employee_no} disabled={!!editing} required placeholder="工号" onChange={(e) => setForm({ ...form, employee_no: e.target.value })} />
          <input value={form.name || ""} placeholder="姓名" onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input value={form.email || ""} placeholder="邮箱" onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <input value={form.department_level1 || ""} placeholder="一级部门" onChange={(e) => setForm({ ...form, department_level1: e.target.value })} />
          <select value={form.primary_role} onChange={(e) => setRole(e.target.value as ManagedUserRole)}>
            <option value="admin">管理员</option>
            <option value="coach">教练</option>
            <option value="student">学员</option>
          </select>
          {form.primary_role === "admin" ? (
            <label className="admin-checkbox"><input type="checkbox" checked={form.is_coach} onChange={(e) => setForm({ ...form, is_coach: e.target.checked })} />兼任教练</label>
          ) : null}
          {form.primary_role === "student" ? (
            <select value={form.coach_id || ""} onChange={(e) => setForm({ ...form, coach_id: e.target.value || null })}>
              <option value="">未分配教练</option>
              {coaches.map((coach) => <option key={coach.id} value={coach.id}>{coach.name || coach.employee_no}</option>)}
            </select>
          ) : null}
          <label className="admin-checkbox"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />启用</label>
          <button className="primary" type="submit" disabled={busy}>{editing ? "保存" : "创建"}</button>
        </form>
      </div>
    </div>
  );
}

function roleLabel(role: ManagedUserRole) {
  return role === "admin" ? "管理员" : role === "coach" ? "教练" : "学员";
}

function formatError(err: unknown) {
  return err instanceof Error ? err.message : "请求失败";
}
```

- [ ] **Step 5: Add admin CSS**

Append to `frontend/app/globals.css`:

```css
.admin-shell { min-height: 100vh; display: grid; grid-template-columns: 240px minmax(0, 1fr); gap: 16px; padding: 18px; }
.admin-sidebar { border: 1px solid var(--line); border-radius: 20px; background: #f8fafc; padding: 18px; display: flex; flex-direction: column; gap: 24px; }
.admin-sidebar h1 { margin: 0; font-size: 22px; }
.admin-sidebar p { margin: 6px 0 0; color: var(--muted); }
.admin-sidebar nav { display: grid; gap: 8px; }
.admin-sidebar a { color: #334155; text-decoration: none; padding: 11px 12px; border-radius: 12px; font-weight: 700; }
.admin-sidebar a.active, .admin-sidebar a:hover { background: #e0f2fe; color: #0369a1; }
.admin-main { min-width: 0; display: flex; flex-direction: column; gap: 14px; }
.admin-topbar { border: 1px solid var(--line); border-radius: 18px; background: #fff; padding: 14px 16px; display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.admin-topbar span { display: block; color: var(--muted); font-size: 12px; margin-top: 4px; }
.admin-topbar-actions { display: flex; gap: 8px; align-items: center; }
.admin-card { border: 1px solid var(--line); border-radius: 18px; background: rgba(255,255,255,0.92); padding: 18px; box-shadow: 0 12px 30px rgba(15, 23, 42, 0.06); }
.admin-card h2, .admin-card h3 { margin: 0 0 8px; }
.admin-muted, .admin-card p { color: var(--muted); }
.admin-stack { display: grid; gap: 14px; }
.admin-section-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.admin-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
.admin-link-button, .admin-file-button { display: inline-flex; align-items: center; justify-content: center; text-decoration: none; border-radius: 12px; padding: 10px 14px; font-weight: 700; cursor: pointer; }
.admin-table-card { overflow: auto; }
.admin-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.admin-table th, .admin-table td { border-bottom: 1px solid var(--line); padding: 11px 10px; text-align: left; vertical-align: top; }
.admin-table th { color: #475569; background: #f8fafc; white-space: nowrap; }
.admin-badge { margin-left: 6px; display: inline-flex; font-size: 12px; color: #0369a1; background: #e0f2fe; border-radius: 999px; padding: 2px 7px; }
.admin-form-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; align-items: center; }
.admin-form-grid input, .admin-form-grid select { width: 100%; border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; font: inherit; background: #fff; }
.admin-checkbox { display: flex; gap: 8px; align-items: center; color: #334155; }
.admin-result { margin-top: 10px; color: #166534; background: #dcfce7; border-radius: 12px; padding: 10px 12px; }
.admin-stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-top: 14px; }
.admin-stat-grid div { background: #f8fafc; border: 1px solid var(--line); border-radius: 14px; padding: 14px; display: grid; gap: 6px; }
.admin-stat-grid span { color: var(--muted); }
.admin-denied { display: grid; place-items: center; grid-template-columns: 1fr; }
@media (max-width: 860px) { .admin-shell { grid-template-columns: 1fr; } .admin-topbar, .admin-section-head { flex-direction: column; align-items: stretch; } }
```

- [ ] **Step 6: Run frontend build**

Run:

```bash
npm --prefix frontend run build
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/app/admin/layout.tsx frontend/app/admin/page.tsx frontend/app/admin/users/page.tsx frontend/app/admin/whitelist/page.tsx frontend/app/globals.css
git commit -m "feat: add admin users interface"
```

---

### Task 8: Build admin conversations page

**Files:**
- Create: `frontend/app/admin/conversations/page.tsx`
- Modify: `frontend/app/globals.css`

- [ ] **Step 1: Create conversation history page**

Create `frontend/app/admin/conversations/page.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";

import { getConversationSession, listConversationSessions, listConversationUsers } from "@/lib/admin";
import { checkAuth } from "@/lib/auth";
import type { AdminConversationDetail, AdminSessionSummary, ConversationUserSummary } from "@/types/admin";
import type { UserInfo } from "@/types/auth";

export default function AdminConversationsPage() {
  const [currentUser, setCurrentUser] = useState<UserInfo | null>(null);
  const [scope, setScope] = useState<"mine" | "all">("all");
  const [users, setUsers] = useState<ConversationUserSummary[]>([]);
  const [selectedUser, setSelectedUser] = useState<ConversationUserSummary | null>(null);
  const [sessions, setSessions] = useState<AdminSessionSummary[]>([]);
  const [detail, setDetail] = useState<AdminConversationDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    checkAuth().then((user) => {
      setCurrentUser(user);
      const initialScope = user?.is_admin && user.is_coach ? "mine" : "all";
      setScope(initialScope);
      void refreshUsers(initialScope);
    });
  }, []);

  async function refreshUsers(nextScope: "mine" | "all") {
    setError("");
    try {
      setUsers(await listConversationUsers(nextScope));
      setSelectedUser(null);
      setSessions([]);
      setDetail(null);
    } catch (err) {
      setError(formatError(err));
    }
  }

  async function chooseUser(user: ConversationUserSummary) {
    setSelectedUser(user);
    setDetail(null);
    setError("");
    try {
      setSessions(await listConversationSessions(user.managed_user_id));
    } catch (err) {
      setError(formatError(err));
    }
  }

  async function chooseSession(sessionId: string) {
    setError("");
    try {
      setDetail(await getConversationSession(sessionId));
    } catch (err) {
      setError(formatError(err));
    }
  }

  function changeScope(nextScope: "mine" | "all") {
    setScope(nextScope);
    void refreshUsers(nextScope);
  }

  const canSwitchAll = currentUser?.is_admin;

  return (
    <div className="admin-stack">
      <div className="admin-card">
        <div className="admin-section-head">
          <div><h2>对话历史</h2><p>按学员查看只读会话记录。</p></div>
          <div className="admin-segmented">
            <button className={scope === "mine" ? "active" : ""} type="button" onClick={() => changeScope("mine")}>我的学员</button>
            {canSwitchAll ? <button className={scope === "all" ? "active" : ""} type="button" onClick={() => changeScope("all")}>全部</button> : null}
          </div>
        </div>
        {error ? <div className="auth-error">{error}</div> : null}
      </div>

      <div className="admin-conversation-grid">
        <div className="admin-card admin-table-card">
          <h3>学员</h3>
          <table className="admin-table">
            <thead><tr><th>工号</th><th>姓名</th><th>一级部门</th><th>会话数</th><th>操作</th></tr></thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.managed_user_id} className={selectedUser?.managed_user_id === user.managed_user_id ? "selected" : ""}>
                  <td>{user.employee_no}</td>
                  <td>{user.name || "-"}</td>
                  <td>{user.department_level1 || "-"}</td>
                  <td>{user.session_count}</td>
                  <td><button className="secondary" type="button" onClick={() => void chooseUser(user)}>查看</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="admin-card admin-table-card">
          <h3>会话列表</h3>
          {selectedUser ? <p className="admin-muted">{selectedUser.name || selectedUser.employee_no}</p> : <p className="admin-muted">请选择学员</p>}
          <table className="admin-table">
            <thead><tr><th>最近消息</th><th>更新时间</th><th>消息数</th><th>操作</th></tr></thead>
            <tbody>
              {sessions.map((session) => (
                <tr key={session.session_id} className={detail?.session_id === session.session_id ? "selected" : ""}>
                  <td>{session.latest_preview}</td>
                  <td>{formatDate(session.updated_at)}</td>
                  <td>{session.message_count}</td>
                  <td><button className="secondary" type="button" onClick={() => void chooseSession(session.session_id)}>详情</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="admin-card">
        <h3>会话详情</h3>
        {detail ? (
          <div className="admin-message-list">
            {detail.history.map((message, index) => (
              <div key={`${message.role}-${index}`} className={`admin-message ${message.role}`}>
                <div className="admin-message-role">{message.role === "assistant" ? "AI 教练" : "用户"}</div>
                <div className="admin-message-body">
                  <p>{message.content}</p>
                  {message.attachments?.length ? (
                    <div className="admin-attachments">
                      {message.attachments.map((file, fileIndex) => <span key={`${file.filename}-${fileIndex}`}>{file.filename} {file.size ? `(${file.size} bytes)` : ""}</span>)}
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        ) : <p className="admin-muted">请选择会话查看详情。</p>}
      </div>
    </div>
  );
}

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString("zh-CN") : "-";
}

function formatError(err: unknown) {
  return err instanceof Error ? err.message : "请求失败";
}
```

- [ ] **Step 2: Add conversation CSS**

Append to `frontend/app/globals.css`:

```css
.admin-conversation-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
.admin-segmented { display: inline-flex; gap: 6px; padding: 4px; background: #e2e8f0; border-radius: 14px; }
.admin-segmented button { border: 0; border-radius: 10px; padding: 8px 12px; background: transparent; cursor: pointer; font-weight: 700; color: #475569; }
.admin-segmented button.active { background: #fff; color: #0369a1; box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08); }
.admin-table tr.selected td { background: #f0f9ff; }
.admin-message-list { display: grid; gap: 12px; }
.admin-message { display: grid; gap: 6px; }
.admin-message-role { font-size: 12px; color: var(--muted); font-weight: 700; }
.admin-message-body { border-radius: 14px; padding: 12px 14px; border: 1px solid var(--line); background: #f8fafc; white-space: pre-wrap; }
.admin-message.user .admin-message-body { background: #e0f2fe; border-color: #bae6fd; }
.admin-message.assistant .admin-message-body { background: #f8fafc; }
.admin-message-body p { margin: 0; color: #0f172a; }
.admin-attachments { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.admin-attachments span { border: 1px solid #cbd5e1; border-radius: 999px; padding: 4px 8px; color: #475569; background: #fff; font-size: 12px; }
@media (max-width: 1080px) { .admin-conversation-grid { grid-template-columns: 1fr; } }
```

- [ ] **Step 3: Run frontend build**

Run:

```bash
npm --prefix frontend run build
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/admin/conversations/page.tsx frontend/app/globals.css
git commit -m "feat: add admin conversation history interface"
```

---

### Task 9: Run backend regression and fix integration mismatches

**Files:**
- Modify only files implicated by failures from the commands below.
- Test: all backend unit and integration tests.

- [ ] **Step 1: Run backend targeted suite**

Run:

```bash
uv run pytest tests/unit/test_managed_user_service.py tests/unit/test_admin_conversation_service.py tests/unit/test_user_service_admin.py tests/integration/test_admin_users_api.py tests/integration/test_admin_conversations_api.py tests/integration/test_admin_whitelist_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run backend unit and integration regression**

Run:

```bash
uv run pytest tests/unit tests/integration -q
```

Expected: PASS.

- [ ] **Step 3: If `auth/me` fails because `current_user.managed_user` is unloaded, query with relationship loading in dependency path**

Modify `app/api/v1/routes/auth.py` by replacing direct access with `getattr` fallback:

```python
    managed = getattr(current_user, "managed_user", None)
```

Expected: tests that use dependency override users without relationships continue to pass.

- [ ] **Step 4: If fake DB tests fail due to `db.get` absence, add `db.get` to the fake used by that test**

Use this method in the relevant fake DB class:

```python
    async def get(self, model, obj_id):
        return None
```

Expected: tests that do not exercise lookup branches pass without changing production code.

- [ ] **Step 5: Commit regression fixes**

If files changed, run:

```bash
git add app tests
git commit -m "test: stabilize admin user management regression"
```

If no files changed, skip this commit.

---

### Task 10: Run frontend build and manual UI verification

**Files:**
- Modify frontend files only if build or manual verification exposes a concrete issue.

- [ ] **Step 1: Run frontend production build**

Run:

```bash
npm --prefix frontend run build
```

Expected: PASS.

- [ ] **Step 2: Start backend and frontend for manual verification**

Run backend in one terminal:

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 2024
```

Run frontend in another terminal:

```bash
npm --prefix frontend run dev
```

Expected: backend listens on `127.0.0.1:2024`; frontend listens on `127.0.0.1:3000`.

- [ ] **Step 3: Verify admin navigation in browser**

Open the local frontend, log in as an admin test account, and verify:

- Chat sidebar user menu shows `管理后台`.
- `/admin` loads the admin shell with `概览`, `用户管理`, `对话历史` navigation.
- Non-admin account cannot use `/admin` and sees `无权限访问`.

- [ ] **Step 4: Verify user management in browser**

In `/admin/users`, verify:

- `下载模板` downloads an xlsx file whose header row is `工号, 姓名, 邮箱, 一级部门, 主角色, 兼任教练, 所属教练工号, 启用状态`.
- Creating a coach shows the coach in the coach dropdown.
- Creating a student allows assigning that coach.
- Editing an admin can toggle `兼任教练`.
- System admin rows show `系统` badge and cannot be disabled by API responses after save.

- [ ] **Step 5: Verify conversation history in browser**

In `/admin/conversations`, verify:

- Admin coach defaults to `我的学员`.
- Admin can switch to `全部`.
- Student summary rows load.
- Selecting a student loads sessions.
- Selecting a session shows read-only user and AI messages plus attachment names.
- There are no export, delete, hide, or note actions.

- [ ] **Step 6: Stop dev servers**

Stop both foreground processes with `Ctrl+C` in their terminals.

- [ ] **Step 7: Commit UI verification fixes**

If files changed, run:

```bash
git add frontend
git commit -m "fix: polish admin panel verification issues"
```

If no files changed, skip this commit.

---

### Task 11: Final verification and cleanup

**Files:**
- No planned edits.

- [ ] **Step 1: Run final backend suite**

Run:

```bash
uv run pytest tests/unit tests/integration -q
```

Expected: PASS.

- [ ] **Step 2: Run final frontend build**

Run:

```bash
npm --prefix frontend run build
```

Expected: PASS.

- [ ] **Step 3: Check git status**

Run:

```bash
git status --short
```

Expected: only intentional untracked local runtime artifacts may remain, such as `.superpowers/`; source files should be committed.

- [ ] **Step 4: Summarize implementation state**

Prepare a final note listing:

- Commits created.
- Backend tests run and result.
- Frontend build result.
- Manual UI verification result.
- Any local artifacts intentionally not committed.

---

## Self-Review

Spec coverage:

- Independent `/admin` backend and navigation: Tasks 7 and 8.
- Managed user profile model, safe migration, and `users.managed_user_id`: Task 1.
- Preserve `users.id`, `chat_sessions.user_id`, and messages: Task 1 migration avoids rewriting existing session links; Task 11 regression confirms.
- CAS access via managed users and system-admin protection: Task 3.
- User management API, template, import, role, department, coach assignment: Tasks 2 and 4.
- Conversation history visibility and read-only APIs: Task 5.
- Frontend users and conversations pages: Tasks 6, 7, and 8.
- Tests and manual browser validation: Tasks 9, 10, and 11.

Placeholder scan targets:

- The plan intentionally contains no placeholder markers, unfinished sections, or deferred feature language.
- Code snippets define every new function referenced by later tasks.

Type consistency:

- Backend role values are `admin | coach | student` throughout service, API, and frontend types.
- Frontend field names match backend row serializers: `department_level1`, `primary_role`, `is_coach`, `coach_id`, `is_system_admin`.
- Conversation endpoint shapes match `frontend/types/admin.ts`.
