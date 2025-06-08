"""remove_display_order_from_key_concepts

Revision ID: e75f42d27f90
Revises: 7f2fb370b4dd
Create Date: 2025-06-07 22:12:12.537181

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e75f42d27f90'
down_revision: Union[str, None] = '7f2fb370b4dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Remove display_order column from key_concepts table
    op.drop_column('key_concepts', 'display_order')


def downgrade() -> None:
    # Add display_order column back if needed
    op.add_column('key_concepts', sa.Column('display_order', sa.Integer(), nullable=True, server_default='0'))
