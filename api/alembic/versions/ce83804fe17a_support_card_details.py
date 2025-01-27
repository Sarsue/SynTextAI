"""support card details

Revision ID: ce83804fe17a
Revises: 8f470e6250f0
Create Date: 2025-01-27 13:12:05.197110

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ce83804fe17a'
down_revision: Union[str, None] = '8f470e6250f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create the card_details table
    op.create_table(
        'card_details',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('subscription_id', sa.Integer, sa.ForeignKey('subscriptions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('card_last4', sa.String(4), nullable=False),
        sa.Column('card_type', sa.String(50), nullable=False),
        sa.Column('exp_month', sa.Integer, nullable=False),
        sa.Column('exp_year', sa.Integer, nullable=False),
        sa.Column('created_at', sa.DateTime, default=sa.func.now()),
    )

def downgrade() -> None:
    # Drop the card_details table
    op.drop_table('card_details')
