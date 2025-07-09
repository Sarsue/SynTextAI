"""Fix is_custom columns to handle existing data

Revision ID: 20250707_fix_is_custom_columns
Revises: d396200e75c3
Create Date: 2025-07-07 15:49:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250707_fix_is_custom_columns'
down_revision = 'd396200e75c3'
branch_labels = None
depends_on = None

def upgrade():
    # For key_concepts table
    op.execute("""
    DO $$
    BEGIN
        -- First, drop the column if it exists (in case the previous migration partially failed)
        IF EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name='key_concepts' AND column_name='is_custom'
        ) THEN
            ALTER TABLE key_concepts DROP COLUMN is_custom;
        END IF;
        
        -- Add the column with a default value
        ALTER TABLE key_concepts 
        ADD COLUMN is_custom BOOLEAN NOT NULL DEFAULT FALSE;
    END $$;
    """)
    
    # For flashcards table
    op.execute("""
    DO $$
    BEGIN
        -- First, drop the column if it exists (in case the previous migration partially failed)
        IF EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name='flashcards' AND column_name='is_custom'
        ) THEN
            ALTER TABLE flashcards DROP COLUMN is_custom;
        END IF;
        
        -- Add the column with a default value
        ALTER TABLE flashcards 
        ADD COLUMN is_custom BOOLEAN NOT NULL DEFAULT FALSE;
    END $$;
    """)
    
    # For quiz_questions table
    op.execute("""
    DO $$
    BEGIN
        -- First, drop the column if it exists (in case the previous migration partially failed)
        IF EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name='quiz_questions' AND column_name='is_custom'
        ) THEN
            ALTER TABLE quiz_questions DROP COLUMN is_custom;
        END IF;
        
        -- Add the column with a default value
        ALTER TABLE quiz_questions 
        ADD COLUMN is_custom BOOLEAN NOT NULL DEFAULT FALSE;
    END $$;
    """)

def downgrade():
    # Drop the columns if they exist
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
        
        IF EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name='flashcards' AND column_name='is_custom'
        ) THEN
            ALTER TABLE flashcards DROP COLUMN is_custom;
        END IF;
        
        IF EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_name='quiz_questions' AND column_name='is_custom'
        ) THEN
            ALTER TABLE quiz_questions DROP COLUMN is_custom;
        END IF;
    END $$;
    """)
