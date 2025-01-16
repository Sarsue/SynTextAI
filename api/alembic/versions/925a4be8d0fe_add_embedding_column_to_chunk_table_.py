"""Add embedding column to Chunk table Link Chunk to Segment for Generation

Revision ID: 925a4be8d0fe
Revises: 85de474e33fd
Create Date: 2025-01-16 11:32:14.603935

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '925a4be8d0fe'
down_revision: Union[str, None] = '85de474e33fd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('segments',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('page_number', sa.Integer(), nullable=True),
    sa.Column('content', sa.String(), nullable=True),
    sa.Column('file_id', sa.Integer(), nullable=True),
    sa.Column('meta_data', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['file_id'], ['files.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id')
    )
    op.add_column('chunks', sa.Column('segment_id', sa.Integer(), nullable=True))
    op.create_foreign_key(None, 'chunks', 'segments', ['segment_id'], ['id'], ondelete='CASCADE')
    op.drop_column('chunks', 'data')
    op.drop_column('chunks', 'content')
    op.create_unique_constraint(None, 'files', ['id'])
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_constraint(None, 'files', type_='unique')
    op.add_column('chunks', sa.Column('content', sa.VARCHAR(), autoincrement=False, nullable=True))
    op.add_column('chunks', sa.Column('data', postgresql.JSON(astext_type=sa.Text()), autoincrement=False, nullable=True))
    op.drop_constraint(None, 'chunks', type_='foreignkey')
    op.drop_column('chunks', 'segment_id')
    op.drop_table('segments')
    # ### end Alembic commands ###
