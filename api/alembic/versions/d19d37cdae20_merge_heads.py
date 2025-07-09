"""merge heads

Revision ID: d19d37cdae20
Revises: 20250707_fix_is_custom_columns, fix_key_concepts_is_custom
Create Date: 2025-07-07 15:52:26.458808

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd19d37cdae20'
down_revision: Union[str, None] = ('20250707_fix_is_custom_columns', 'fix_key_concepts_is_custom')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
