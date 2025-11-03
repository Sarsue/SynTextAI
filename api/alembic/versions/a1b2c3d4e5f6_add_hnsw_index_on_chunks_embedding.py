"""Add HNSW index on chunks.embedding and supporting indexes

Revision ID: a1b2c3d4e5f6
Revises: 925a4be8d0fe
Create Date: 2025-11-02 21:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '925a4be8d0fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ensure pgvector extension exists
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create supporting btree indexes for tenant-scoped search
    op.create_index('idx_files_user_id', 'files', ['user_id'], unique=False)
    op.create_index('idx_chunks_file_id', 'chunks', ['file_id'], unique=False)

    # Create ANN index on the embedding column with proper operator class.
    # Prefer HNSW (pgvector >= 0.7 with hnsw support). Fallback to IVFFLAT if HNSW not available.
    op.execute(
        """
        DO $$
        BEGIN
            BEGIN
                -- Try HNSW with L2 distance
                EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON chunks USING hnsw (embedding vector_l2_ops)';
            EXCEPTION WHEN OTHERS THEN
                -- Fallback to IVFFLAT with L2 distance
                EXECUTE 'CREATE INDEX IF NOT EXISTS idx_chunks_embedding_ivfflat ON chunks USING ivfflat (embedding vector_l2_ops)';
            END;
        END $$;
        """
    )


def downgrade() -> None:
    # Drop indexes in reverse order
    op.execute("DROP INDEX IF EXISTS idx_chunks_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS idx_chunks_embedding_ivfflat")
    op.drop_index('idx_chunks_file_id', table_name='chunks')
    op.drop_index('idx_files_user_id', table_name='files')
