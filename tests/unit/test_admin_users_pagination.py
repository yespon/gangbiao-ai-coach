import uuid

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.db_models import ManagedUserDB
from app.services.managed_user_service import (
    ManagedUserListFilters,
    build_managed_user_filtered_stmt,
)


def _compile_where(stmt):
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    return str(compiled)


def test_filter_builder_text_query_matches_haystack():
    filters = ManagedUserListFilters(q="alice", role=None, enabled=None,
                                     coach_filter="all", department_level1=None, has_email=None)
    stmt = build_managed_user_filtered_stmt(filters)
    sql = _compile_where(stmt).lower()
    assert "lower(" in sql and "alice" in sql


def test_filter_builder_coach_filter_unassigned():
    filters = ManagedUserListFilters(q=None, role=None, enabled=None,
                                     coach_filter="unassigned", department_level1=None, has_email=None)
    stmt = build_managed_user_filtered_stmt(filters)
    sql = _compile_where(stmt).lower()
    assert "coach_id is null" in sql


def test_filter_builder_coach_filter_specific_id():
    cid = uuid.uuid4()
    filters = ManagedUserListFilters(q=None, role=None, enabled=None,
                                     coach_filter=str(cid), department_level1=None, has_email=None)
    stmt = build_managed_user_filtered_stmt(filters)
    sql = _compile_where(stmt)
    assert str(cid) in sql


def test_filter_builder_has_email_true():
    filters = ManagedUserListFilters(q=None, role=None, enabled=None,
                                     coach_filter="all", department_level1=None, has_email=True)
    stmt = build_managed_user_filtered_stmt(filters)
    sql = _compile_where(stmt).lower()
    assert "email is not null" in sql
