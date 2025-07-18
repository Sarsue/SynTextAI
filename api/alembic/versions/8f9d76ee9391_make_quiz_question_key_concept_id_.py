"""make quiz_question key_concept_id nullable

Revision ID: 8f9d76ee9391
Revises: b3f6f949b5af
Create Date: 2025-06-23 15:26:12.253454

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f9d76ee9391'
down_revision: Union[str, None] = 'b3f6f949b5af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('quiz_questions', 'key_concept_id', nullable=True)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('quiz_questions', 'key_concept_id', nullable=False)
    # ### end Alembic commands ###
