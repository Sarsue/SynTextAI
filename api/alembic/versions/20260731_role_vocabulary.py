"""One word for read-only access: staff.

Three vocabularies were in play for the same idea. organization_members.role
used 'member', workspace_members.role used 'staff', and the capability table
carried both so they would not disagree. Nobody outside the code knew which was
which, and the members list showed the raw value, so a read-only person was
labelled 'member' on one screen and 'staff' on another.

Two roles, said the same way everywhere:

    admin   can manage: upload and delete documents, manage workspaces and
            people
    staff   can read: ask questions and read answers, change nothing

owner remains what it is: an admin who also pays.

Revision ID: 20260731_roles
Revises: 20260731_scope
"""
from alembic import op


revision = '20260731_roles'
down_revision = '20260731_scope'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE organization_members SET role = 'staff' WHERE role = 'member'")


def downgrade():
    op.execute("UPDATE organization_members SET role = 'member' WHERE role = 'staff'")
