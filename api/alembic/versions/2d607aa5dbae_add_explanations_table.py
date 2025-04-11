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
    pass


def downgrade() -> None:
    pass
