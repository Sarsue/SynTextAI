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
    pass


def downgrade() -> None:
    pass
