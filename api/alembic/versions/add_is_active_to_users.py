"""Add is_active column to users table

Revision ID: 2ab18ff427dc
Revises: 1ab07ff317db
Create Date: 2025-10-01 13:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2ab18ff427dc'
down_revision: str = '1ab07ff317db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add is_active column with default value True
    op.add_column('users', 
                 sa.Column('is_active', 
                          sa.Boolean(), 
                          server_default='true', 
                          nullable=False))


def downgrade() -> None:
    # Remove the is_active column
    op.drop_column('users', 'is_active')
