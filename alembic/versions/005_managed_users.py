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
