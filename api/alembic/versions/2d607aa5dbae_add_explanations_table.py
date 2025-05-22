"""add_explanations_table

Revision ID: 2d607aa5dbae
Revises: faadb2f9e613
Create Date: 2025-04-11 14:54:10.912212

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d607aa5dbae'
down_revision: Union[str, None] = 'faadb2f9e613'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This table was automatically created by SQLAlchemy when the application started
    # This migration documents that this table already exists
    
    # The following would create the table if it didn't exist:
    op.create_table('explanations',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('file_id', sa.Integer(), nullable=True),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('content', sa.Text(), nullable=True),
        sa.Column('explanation', sa.Text(), nullable=True),
        sa.Column('page', sa.Integer(), nullable=True),
        sa.Column('video_start', sa.Float(), nullable=True),
        sa.Column('video_end', sa.Float(), nullable=True),
        sa.Column('selection_type', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['file_id'], ['files.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    # Drop the explanations table if needed to rollback this migration
    op.drop_table('explanations')
