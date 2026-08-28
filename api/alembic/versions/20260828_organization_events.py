"""Record what happened to a team, not only what it looks like now.

organization_members and workspace_invites hold current state and nothing else.
A removal leaves no trace at all, and an invite's history is its status column
being overwritten in place, so "who removed Victor, and when" had no answer.

Revision ID: 20260828_organization_events
Revises: 20260826_chunk_embedding_model
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_organization_events"
down_revision: Union[str, None] = "20260826_chunk_embedding_model"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organization_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Who did it. SET NULL rather than CASCADE: the event is a record of
        # something that happened to the organization, and it stays true after
        # the person who did it is gone. Deleting their account must not quietly
        # delete the history of what they did.
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Who it was done to, by id where one exists and by address always. An
        # invite names an address before there is any account, and a removed
        # person may later delete their account, so the address is the part that
        # survives and is what the reader recognises.
        sa.Column(
            "subject_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("subject_email", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        # Role, scope, workspace ids: whatever the event needs to be readable a
        # year later, without a migration every time an event gains a field.
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # The only query this table serves: one organization, newest first.
    op.create_index(
        "ix_organization_events_org_created",
        "organization_events",
        ["organization_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_organization_events_org_created", table_name="organization_events")
    op.drop_table("organization_events")
