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
