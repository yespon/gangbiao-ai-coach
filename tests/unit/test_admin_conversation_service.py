import uuid

from app.models.db_models import ManagedUserDB, User
from app.services.admin_conversation_service import (
    can_view_student,
    default_conversation_scope,
    is_admin_user,
    is_coach_user,
)


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


def test_plain_student_cannot_view_another_student_with_mine_scope():
    student_user = _login_user(ManagedUserDB(id=uuid.uuid4(), primary_role="student", is_coach=False))
    other_student = ManagedUserDB(id=uuid.uuid4(), primary_role="student", coach_id=uuid.uuid4())
    assert can_view_student(student_user, other_student, "mine") is False
