"""Scope chat histories to a workspace.

Conversations were keyed to a user only, while documents are keyed to a
workspace. Switching workspace therefore changed the documents but not the
conversation list, so a thread started in one workspace could be continued in
another and answer from a different set of documents — one thread, blended
sources, citations pointing at files the current workspace cannot list.

Existing rows are backfilled to each user's earliest workspace rather than left
NULL: "visible in every workspace" would make the same confusion permanent.

Revision ID: 20260731_chat_ws
Revises: 20260730_orgs
"""
from alembic import op
import sqlalchemy as sa


revision = '20260731_chat_ws'
down_revision = '20260730_orgs'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'chat_histories',
        sa.Column('workspace_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'chat_histories_workspace_id_fkey',
        'chat_histories', 'workspaces',
        ['workspace_id'], ['id'],
        ondelete='CASCADE',
    )
    # The list query filters on (user_id, workspace_id) on every page load.
    op.create_index(
        'ix_chat_histories_user_workspace',
        'chat_histories',
        ['user_id', 'workspace_id'],
    )

    # Backfill: each conversation joins the owner's earliest workspace, which is
    # the one that existed when it was created in every account that has only
    # ever had one.
    op.execute("""
        UPDATE chat_histories ch
        SET workspace_id = sub.workspace_id
        FROM (
            SELECT user_id, MIN(id) AS workspace_id
            FROM workspaces
            GROUP BY user_id
        ) AS sub
        WHERE ch.user_id = sub.user_id
          AND ch.workspace_id IS NULL
    """)

    # Left deliberately nullable. A user with no workspace at all still has
    # conversations, and failing their history load is worse than showing it
    # unscoped; the list query treats NULL as "belongs to no workspace".


def downgrade():
    op.drop_index('ix_chat_histories_user_workspace', table_name='chat_histories')
    op.drop_constraint('chat_histories_workspace_id_fkey', 'chat_histories', type_='foreignkey')
    op.drop_column('chat_histories', 'workspace_id')
