"""feedback submissions + attachments

Revision ID: 006_feedback_tables
Revises: 005_managed_users
Create Date: 2026-06-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID


revision: str = "006_feedback_tables"
down_revision: Union[str, Sequence[str], None] = "005_managed_users"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feedback_submissions",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), server_default=sa.text("'open'"), nullable=False),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("ip", sa.String(64), nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("read_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_at", TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_feedback_submissions_user_id", "feedback_submissions", ["user_id"])
    op.create_index("ix_feedback_submissions_status", "feedback_submissions", ["status"])
    op.create_index("ix_feedback_submissions_created_at", "feedback_submissions", ["created_at"])

    op.create_table(
        "feedback_attachments",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("feedback_id", UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("saved_path", sa.Text(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(["feedback_id"], ["feedback_submissions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("feedback_id", "position", name="uq_feedback_attachments_feedback_position"),
    )
    op.create_index("ix_feedback_attachments_feedback_id", "feedback_attachments", ["feedback_id"])


def downgrade() -> None:
    op.drop_index("ix_feedback_attachments_feedback_id", table_name="feedback_attachments")
    op.drop_table("feedback_attachments")
    op.drop_index("ix_feedback_submissions_created_at", table_name="feedback_submissions")
    op.drop_index("ix_feedback_submissions_status", table_name="feedback_submissions")
    op.drop_index("ix_feedback_submissions_user_id", table_name="feedback_submissions")
    op.drop_table("feedback_submissions")
