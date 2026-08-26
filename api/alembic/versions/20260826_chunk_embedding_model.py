"""Record which embedding model wrote each vector.

THE FAILURE THIS CLOSES

A vector only means anything beside other vectors from the same model. Embed the
documents with one model and the questions with another and nothing errors: the
dimensions match, the distance calculation is happy, and the answer cites
confidently from whatever landed nearest. On the benchmark corpus in workspace
4219, 11 of 17 single-document questions stopped retrieving their source at all,
and nothing anywhere said so.

Detecting it needed `reembed_chunks.py --check`, which embeds a sample chunk per
workspace and compares the cosine against the stored vector. That works, but it
costs an inference call per workspace, it has to be remembered, and it had never
been run in production.

With the model written down, staleness is a column comparison. Free, exact, and
available to any query that wants it, including the one that renders a document
in the customer's own list.

NULL is not "unknown", it is "written before this column existed", which means
Voyage, which means stale. Left nullable and backfilled with nothing on purpose:
inventing a model name for rows nobody measured would be worse than admitting
they predate the record.

Revision ID: 20260826_chunk_embedding_model
Revises: 20260826_generated_documents
"""
from alembic import op
import sqlalchemy as sa

revision = '20260826_chunk_embedding_model'
down_revision = '20260826_generated_documents'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('chunks', sa.Column('embedding_model', sa.String(), nullable=True))
    # Every query that asks "what is stale here" filters on this, per workspace,
    # so it is worth an index rather than a sequential scan of every chunk.
    op.create_index('idx_chunks_embedding_model', 'chunks', ['embedding_model'])


def downgrade():
    op.drop_index('idx_chunks_embedding_model', table_name='chunks')
    op.drop_column('chunks', 'embedding_model')
