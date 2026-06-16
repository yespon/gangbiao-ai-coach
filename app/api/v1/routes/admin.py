"""Admin API — requires admin role."""

from io import BytesIO
from typing import Any
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db
from app.core.config import get_admin_employee_no_set
from app.models.db_models import ManagedUserDB, SsoUserWhitelistDB, User
from app.services.admin_conversation_service import (
    get_conversation_session,
    list_conversation_students,
    list_student_sessions,
)
from app.services.managed_user_service import (
    build_managed_user_template,
    existing_coach_employee_nos,
    is_effective_coach,
    parse_managed_user_excel,
    resolve_import_coach_links,
    upsert_managed_user,
)
from app.services.whitelist_service import (
    MAX_WHITELIST_UPLOAD_BYTES,
    build_whitelist_template,
    normalize_employee_no,
    parse_whitelist_excel,
    upsert_whitelist_entry,
)

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_admin(user: User = Depends(get_current_user)) -> User:
    managed = getattr(user, "managed_user", None)
    if not user.is_admin and not (managed and managed.primary_role == "admin"):
        raise HTTPException(status_code=403, detail="admin_required")
    return user


class WhitelistCreateRequest(BaseModel):
    employee_no: str
    email: str | None = None


class WhitelistPatchRequest(BaseModel):
    enabled: bool | None = None
    email: str | None = None


class ManagedUserRequest(BaseModel):
    employee_no: str
    name: str | None = None
    email: str | None = None
    department_level1: str | None = None
    primary_role: str = "student"
    is_coach: bool = False
    coach_id: str | uuid.UUID | None = None
    enabled: bool = True


class ManagedUserPatchRequest(BaseModel):
    employee_no: str | None = None
    name: str | None = None
    email: str | None = None
    department_level1: str | None = None
    primary_role: str | None = None
    is_coach: bool | None = None
    coach_id: str | uuid.UUID | None = None
    enabled: bool | None = None


def _dt(value: Any) -> str:
    return value.isoformat() if value else ""


def _row(e: SsoUserWhitelistDB) -> dict:
    return {
        "id": str(e.id),
        "employee_no": e.employee_no,
        "email": e.email,
        "enabled": e.enabled,
        "source": e.source,
        "created_at": _dt(e.created_at),
        "updated_at": _dt(e.updated_at),
    }


def _managed_payload_from_profile(profile: ManagedUserDB) -> dict[str, Any]:
    return {
        "employee_no": profile.employee_no,
        "name": profile.name,
        "email": profile.email,
        "department_level1": profile.department_level1,
        "primary_role": profile.primary_role,
        "is_coach": profile.is_coach,
        "coach_id": profile.coach_id,
        "enabled": profile.enabled,
    }


def _managed_user_row(e: ManagedUserDB) -> dict[str, Any]:
    coach = e.__dict__.get("coach")
    return {
        "id": str(e.id),
        "employee_no": e.employee_no,
        "name": e.name,
        "email": e.email,
        "department_level1": e.department_level1,
        "primary_role": e.primary_role,
        "is_coach": e.is_coach,
        "coach_id": str(e.coach_id) if e.coach_id else None,
        "coach_name": getattr(coach, "name", None) if coach else None,
        "enabled": e.enabled,
        "source": e.source,
        "is_system_admin": e.employee_no in get_admin_employee_no_set(),
        "created_at": _dt(getattr(e, "created_at", None)),
        "updated_at": _dt(getattr(e, "updated_at", None)),
    }


def _coach_row(e: ManagedUserDB) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "employee_no": e.employee_no,
        "name": e.name,
        "department_level1": e.department_level1,
        "primary_role": e.primary_role,
        "is_coach": e.is_coach,
    }


def _matches_filters(e: ManagedUserDB, q: str | None, role: str | None, enabled: bool | None) -> bool:
    if role and e.primary_role != role:
        return False
    if enabled is not None and e.enabled != enabled:
        return False
    if q:
        needle = q.strip().lower()
        haystack = [e.employee_no, e.name, e.email, e.department_level1]
        if not any(needle in str(value).lower() for value in haystack if value):
            return False
    return True


@router.get("/users")
async def list_managed_users(
    q: str | None = None,
    role: str | None = None,
    enabled: bool | None = None,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    stmt = (
        select(ManagedUserDB)
        .options(selectinload(ManagedUserDB.coach))
        .order_by(ManagedUserDB.updated_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [_managed_user_row(e) for e in rows if _matches_filters(e, q, role, enabled)]


@router.post("/users")
async def create_managed_user(
    body: ManagedUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    profile, _created = await upsert_managed_user(
        db,
        body.model_dump(),
        "manual",
        admin.id,
        get_admin_employee_no_set(),
    )
    await db.commit()
    await db.refresh(profile)
    return _managed_user_row(profile)


@router.get("/users/template")
async def managed_users_template(admin: User = Depends(require_admin)):
    return StreamingResponse(
        BytesIO(build_managed_user_template()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=managed-users-template.xlsx"},
    )


@router.get("/users/coaches")
async def list_managed_user_coaches(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await db.execute(select(ManagedUserDB).where(ManagedUserDB.enabled.is_(True)))
    rows = result.scalars().all()
    return [_coach_row(e) for e in rows if e.enabled and is_effective_coach(e.primary_role, e.is_coach)]


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
    link_errors = resolve_import_coach_links(parsed.rows, await existing_coach_employee_nos(db))
    if link_errors:
        return {"created": 0, "updated": 0, "skipped": len(parsed.errors) + len(link_errors), "errors": parsed.errors + link_errors}

    created = updated = 0
    result = await db.execute(select(ManagedUserDB).where(ManagedUserDB.primary_role.in_(["admin", "coach"])))
    coach_id_by_employee_no: dict[str, uuid.UUID] = {
        profile.employee_no: profile.id
        for profile in result.scalars().all()
        if getattr(profile, "id", None) and is_effective_coach(profile.primary_role, profile.is_coach)
    }
    pending_students: list[dict[str, Any]] = []
    admin_employee_nos = get_admin_employee_no_set()

    for row in parsed.rows:
        row = dict(row)
        coach_employee_no = row.pop("coach_employee_no", None)
        row.pop("row", None)
        if row["primary_role"] == "student":
            row["_coach_employee_no"] = coach_employee_no
            pending_students.append(row)
            continue
        profile, was_created = await upsert_managed_user(db, row, "excel", admin.id, admin_employee_nos)
        if hasattr(db, "flush"):
            await db.flush()
        if getattr(profile, "id", None):
            coach_id_by_employee_no[profile.employee_no] = profile.id
        created += 1 if was_created else 0
        updated += 0 if was_created else 1

    for row in pending_students:
        coach_employee_no = row.pop("_coach_employee_no", None)
        if coach_employee_no and coach_employee_no in coach_id_by_employee_no:
            row["coach_id"] = coach_id_by_employee_no[coach_employee_no]
        profile, was_created = await upsert_managed_user(db, row, "excel", admin.id, admin_employee_nos)
        created += 1 if was_created else 0
        updated += 0 if was_created else 1

    await db.commit()
    return {"created": created, "updated": updated, "skipped": len(parsed.errors), "errors": parsed.errors}


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
    payload = _managed_payload_from_profile(profile)
    payload.update(body.model_dump(exclude_unset=True))
    updated, _created = await upsert_managed_user(
        db,
        payload,
        "manual",
        admin.id,
        get_admin_employee_no_set(),
    )
    await db.commit()
    await db.refresh(updated)
    return _managed_user_row(updated)


@router.get("/whitelist")
async def list_whitelist(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = await db.execute(select(SsoUserWhitelistDB).order_by(SsoUserWhitelistDB.updated_at.desc()))
    return [_row(e) for e in result.scalars().all()]


@router.post("/whitelist")
async def add_whitelist(
    body: WhitelistCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    employee_no = normalize_employee_no(body.employee_no)
    if not employee_no:
        raise HTTPException(status_code=400, detail="工号不能为空")
    entry, _created = await upsert_whitelist_entry(db, employee_no, body.email.strip() if body.email else None, "manual", admin.id)
    await db.commit()
    await db.refresh(entry)
    return _row(entry)


@router.patch("/whitelist/{entry_id}")
async def patch_whitelist(
    entry_id: uuid.UUID,
    body: WhitelistPatchRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    entry = await db.get(SsoUserWhitelistDB, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="not_found")
    if body.enabled is not None:
        entry.enabled = body.enabled
    if body.email is not None:
        entry.email = body.email.strip() or None
    await db.commit()
    await db.refresh(entry)
    return _row(entry)


@router.get("/whitelist/template")
async def whitelist_template(admin: User = Depends(require_admin)):
    return StreamingResponse(
        BytesIO(build_whitelist_template()),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=whitelist-template.xlsx"},
    )


@router.post("/whitelist/import")
async def import_whitelist(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="仅支持 .xlsx 文件")
    raw = await file.read(MAX_WHITELIST_UPLOAD_BYTES + 1)
    if len(raw) > MAX_WHITELIST_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="文件过大")
    parsed = parse_whitelist_excel(raw)
    created = updated = 0
    for row in parsed.rows:
        entry, was_created = await upsert_whitelist_entry(db, row["employee_no"], row["email"], "excel", admin.id)
        created += 1 if was_created else 0
        updated += 0 if was_created else 1
    await db.commit()
    return {"created": created, "updated": updated, "skipped": len(parsed.errors), "errors": parsed.errors}


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
