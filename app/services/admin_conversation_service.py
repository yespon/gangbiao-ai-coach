import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.db_models import ChatSessionDB, ManagedUserDB, User
from app.services.managed_user_service import is_effective_coach
from app.services.session_service import db_session_history_for_client, db_session_summary_for_client

VALID_CONVERSATION_SCOPES = {"mine", "all"}


def is_admin_user(user: User) -> bool:
    managed = getattr(user, "managed_user", None)
    return bool(getattr(user, "is_admin", False) or (managed and managed.primary_role == "admin"))


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


def _student_row(student: ManagedUserDB, session_count: int, latest_session_at: Any) -> dict[str, Any]:
    return {
        "managed_user_id": str(student.id),
        "employee_no": student.employee_no,
        "name": student.name,
        "department_level1": student.department_level1,
        "coach_id": str(student.coach_id) if student.coach_id else None,
        "session_count": session_count,
        "latest_session_at": latest_session_at.isoformat() if latest_session_at else None,
    }


async def list_conversation_students(db: AsyncSession, user: User, scope: str) -> list[dict[str, Any]]:
    if scope not in VALID_CONVERSATION_SCOPES:
        raise HTTPException(status_code=400, detail="invalid_scope")
    if scope == "all" and not is_admin_user(user):
        raise HTTPException(status_code=403, detail="conversation_forbidden")

    stmt = select(ManagedUserDB).where(ManagedUserDB.primary_role == "student").order_by(ManagedUserDB.employee_no)
    if scope == "mine":
        managed = getattr(user, "managed_user", None)
        if not managed or not is_coach_user(user):
            raise HTTPException(status_code=403, detail="conversation_forbidden")
        stmt = stmt.where(ManagedUserDB.coach_id == managed.id)

    result = await db.execute(stmt)
    students = list(result.scalars().all())
    if not students:
        return []

    student_ids = [student.id for student in students]
    session_result = await db.execute(
        select(
            User.managed_user_id,
            func.count(ChatSessionDB.id).label("session_count"),
            func.max(ChatSessionDB.updated_at).label("latest_session_at"),
        )
        .join(ChatSessionDB, ChatSessionDB.user_id == User.id)
        .where(User.managed_user_id.in_(student_ids))
        .group_by(User.managed_user_id)
    )
    stats = {row.managed_user_id: (row.session_count, row.latest_session_at) for row in session_result.all()}
    return [_student_row(student, *stats.get(student.id, (0, None))) for student in students]


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
    rows: list[dict[str, Any]] = []
    for session in result.scalars().all():
        summary = db_session_summary_for_client(session)
        summary["message_count"] = len([message for message in session.messages if message.visible_in_history])
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
