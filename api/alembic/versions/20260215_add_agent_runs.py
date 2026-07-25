"""Add agent_runs table for durable agent orchestration

Revision ID: 20260215_add_agent_runs
Revises: c9530c25560e
Create Date: 2026-02-15

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "20260215_add_agent_runs"
down_revision: Union[str, None] = "c9530c25560e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "agent_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("run_type", sa.String(), nullable=False, index=False),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("agent_version", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True),
        sa.Column("file_id", sa.Integer(), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=True),
        sa.Column("chat_history_id", sa.Integer(), sa.ForeignKey("chat_histories.id", ondelete="CASCADE"), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("locked_by", sa.String(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "idx_agent_runs_queue",
        "agent_runs",
        ["status", "priority", "run_after", "created_at"],
        unique=False,
    )
    op.create_index("idx_agent_runs_file_id", "agent_runs", ["file_id"], unique=False)
    op.create_index("idx_agent_runs_user_id", "agent_runs", ["user_id"], unique=False)
    op.create_index("idx_agent_runs_chat_history_id", "agent_runs", ["chat_history_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_agent_runs_chat_history_id", table_name="agent_runs")
    op.drop_index("idx_agent_runs_user_id", table_name="agent_runs")
    op.drop_index("idx_agent_runs_file_id", table_name="agent_runs")
    op.drop_index("idx_agent_runs_queue", table_name="agent_runs")
    op.drop_table("agent_runs")
