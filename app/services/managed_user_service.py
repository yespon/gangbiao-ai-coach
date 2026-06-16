from dataclasses import dataclass
from io import BytesIO
from typing import Any
import uuid

from openpyxl import Workbook

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


def normalize_managed_user_role(
    primary_role: str | None, is_coach: bool, coach_id: str | uuid.UUID | None
):
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
