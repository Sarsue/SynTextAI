"""fix_key_concepts

Revision ID: fix_key_concepts
Revises: e75f42d27f90
Create Date: 2025-06-08 07:40:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fix_key_concepts'
down_revision = 'e75f42d27f90'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This is a fix migration to ensure display_order is removed
    # First check if the column exists to avoid errors
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('key_concepts')]
    
    # Only drop if it exists
    if 'display_order' in columns:
        op.drop_column('key_concepts', 'display_order')


def downgrade() -> None:
    # Add display_order column back if needed
    # Use server_default to set default value for existing rows
    op.add_column('key_concepts', sa.Column('display_order', sa.Integer(), nullable=True, server_default='0'))
