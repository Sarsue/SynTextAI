"""merge heads a1b2c3d4e5f6 and d19d37cdae20

Revision ID: 0153f5c38158
Revises: a1b2c3d4e5f6, d19d37cdae20
Create Date: 2025-11-02 21:09:29.229871

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0153f5c38158'
down_revision: Union[str, None] = ('a1b2c3d4e5f6', 'd19d37cdae20')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
