"""Add processing_status to files table

Revision ID: 20250617_add_ps
Revises: e75f42d27f90
Create Date: 2025-06-17

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250617_add_ps'
down_revision = 'fix_key_concepts'  # Updated to depend on fix_key_concepts
branch_labels = None
depends_on = None

# Define the enum values as strings for database compatibility
processing_status_values = ('uploaded', 'extracting', 'extracted', 'processing', 'processed', 'failed')

def upgrade():
    # Create the enum type in PostgreSQL
    op.execute(f"CREATE TYPE file_processing_status AS ENUM {str(processing_status_values)}")
    
    # Add the column with the enum type and default value
    op.add_column('files', 
                  sa.Column('processing_status', 
                            sa.Enum(*processing_status_values, name='file_processing_status'),
                            nullable=False,
                            server_default='uploaded'))
    
    # Add an index for efficient querying by status
    op.create_index('idx_files_processing_status', 'files', ['processing_status'])
    
    # Add file_type column if it doesn't exist
    op.add_column('files',
                  sa.Column('file_type', sa.String(), nullable=True))

def downgrade():
    # Drop the index
    op.drop_index('idx_files_processing_status')
    
    # Drop the column
    op.drop_column('files', 'processing_status')
    
    # Drop the file_type column
    op.drop_column('files', 'file_type')
    
    # Drop the enum type
    op.execute("DROP TYPE file_processing_status")
