"""Fix is_custom column in key_concepts table

Revision ID: fix_key_concepts_is_custom
Revises: bae9d2d5f0f8
Create Date: 2025-07-07 19:51:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'fix_key_concepts_is_custom'
down_revision = 'bae9d2d5f0f8'
branch_labels = None
depends_on = None

def upgrade():
    # First, check if the column exists and drop it if it does
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name='key_concepts' AND column_name='is_custom'
        ) THEN
            ALTER TABLE key_concepts DROP COLUMN is_custom;
        END IF;
    END $$;
    """)
    
    # Add the column as nullable first
    op.add_column('key_concepts', sa.Column('is_custom', sa.Boolean(), nullable=True))
    
    # Set default value for existing rows
    op.execute("UPDATE key_concepts SET is_custom = FALSE WHERE is_custom IS NULL")
    
    # Now alter the column to be NOT NULL
    op.alter_column('key_concepts', 'is_custom', nullable=False, server_default='false')

def downgrade():
    # Drop the column
    op.drop_column('key_concepts', 'is_custom')
