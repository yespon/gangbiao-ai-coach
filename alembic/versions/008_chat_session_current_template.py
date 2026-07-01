"""Add current_template_id to chat_sessions.

Revision ID: 008_current_template
Revises: 007_session_title_pin_delete
"""

from alembic import op
import sqlalchemy as sa


revision = "008_current_template"
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
