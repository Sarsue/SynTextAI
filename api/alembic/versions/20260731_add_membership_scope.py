"""Let a member be invited to a whole organization or to one workspace.

Before this, belonging to an organization granted every workspace in it, so
workspace_members could only ever widen access, never narrow it. Inviting
somebody to one workspace silently gave them all of them.

Both reaches are now expressible. organization_members.scope says which:

    'organization'  every workspace in the tenant, present and future
    'workspace'     only the workspaces they were explicitly added to

Existing members default to 'workspace', the narrower of the two. That is the
safe direction to be wrong in, and it matches how they were actually invited:
every invite issued so far named a specific workspace.

Invites gain the same choice. workspace_id becomes nullable, because an
organization-wide invite is not about any one workspace, and organization_id is
added so such an invite still knows the tenant it belongs to.

Revision ID: 20260731_scope
Revises: 20260731_plan_key
"""
from alembic import op
import sqlalchemy as sa


revision = '20260731_scope'
down_revision = '20260731_plan_key'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'organization_members',
        sa.Column('scope', sa.String(), nullable=False, server_default='workspace'),
    )

    op.add_column('workspace_invites', sa.Column('organization_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'workspace_invites_organization_id_fkey',
        'workspace_invites', 'organizations',
        ['organization_id'], ['id'],
        ondelete='CASCADE',
    )
    # Every invite so far targeted a workspace, so its organization is known.
    op.execute("""
        UPDATE workspace_invites i
        SET organization_id = w.organization_id
        FROM workspaces w
        WHERE i.workspace_id = w.id AND i.organization_id IS NULL
    """)

    # An organization-wide invite names no workspace.
    op.alter_column('workspace_invites', 'workspace_id', existing_type=sa.Integer(), nullable=True)


def downgrade():
    # Organization-wide invites cannot be represented once workspace_id is
    # mandatory again, so drop them rather than fail on the NOT NULL.
    op.execute("DELETE FROM workspace_invites WHERE workspace_id IS NULL")
    op.alter_column('workspace_invites', 'workspace_id', existing_type=sa.Integer(), nullable=False)
    op.drop_constraint('workspace_invites_organization_id_fkey', 'workspace_invites', type_='foreignkey')
    op.drop_column('workspace_invites', 'organization_id')
    op.drop_column('organization_members', 'scope')
