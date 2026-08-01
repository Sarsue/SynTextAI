"""A document belongs to the workspace, not to whoever uploaded it.

files.user_id cascaded on delete, so removing a person removed every document
they had ever uploaded — including documents sitting in a company workspace
that person did not own. Their chunks and segments went with them, and the
stored objects were deleted from the bucket. Offboarding an employee destroyed
the company's own knowledge base.

Access already follows the workspace: that is how an invited colleague reads a
document the owner uploaded. Deletion now follows it too. user_id becomes
nullable and is cleared rather than cascading, so a document outlives the
account that added it and simply stops naming an uploader.

Documents in workspaces that are themselves being deleted still go, via
workspaces.organization_id and files.workspace_id, both of which cascade.

Revision ID: 20260801_docs
Revises: 20260731_roles
"""
from alembic import op
import sqlalchemy as sa


revision = '20260801_docs'
down_revision = '20260731_roles'
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column('files', 'user_id', existing_type=sa.Integer(), nullable=True)
    op.drop_constraint('files_user_id_fkey', 'files', type_='foreignkey')
    op.create_foreign_key(
        'files_user_id_fkey', 'files', 'users',
        ['user_id'], ['id'],
        ondelete='SET NULL',
    )


def downgrade():
    # Rows whose uploader is gone cannot satisfy NOT NULL again, and inventing
    # an owner for them would be worse than refusing.
    op.execute("DELETE FROM files WHERE user_id IS NULL")
    op.drop_constraint('files_user_id_fkey', 'files', type_='foreignkey')
    op.create_foreign_key(
        'files_user_id_fkey', 'files', 'users',
        ['user_id'], ['id'],
        ondelete='CASCADE',
    )
    op.alter_column('files', 'user_id', existing_type=sa.Integer(), nullable=False)
