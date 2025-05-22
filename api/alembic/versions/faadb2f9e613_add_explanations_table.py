"""add_explanations_table

Revision ID: faadb2f9e613
Revises: 21c6be8c491b
Create Date: 2025-04-11 14:53:01.386258

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'faadb2f9e613'
down_revision: Union[str, None] = '21c6be8c491b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
