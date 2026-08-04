"""Index the things retrieval actually searches.

There was no index on chunks.embedding. None at all: the only entry on the
table was its primary key. Every question in the product sequentially scanned
every chunk in the workspace, computed a cosine distance for each, re-tokenised
every segment's text with to_tsvector, and sorted the lot.

Measured on the benchmark workspace before this migration, at 316 chunks:

    Limit (actual time=170.654..171.583 rows=25)
      Sort Method: top-N heapsort
        Hash Left Join (actual time=14.746..166.813 rows=316)
          Seq Scan on chunks

171ms, of which about 150 is re-tokenising text that has not changed since it
was uploaded. That is linear in corpus size, so a customer with forty
documents pays roughly two seconds per question in the database alone, before
any model is called.

WHY AN INDEX ALONE WOULD HAVE DONE NOTHING

An HNSW index answers `ORDER BY embedding <=> :q LIMIT n`. It cannot answer
`ORDER BY 0.7 * vector_score + 0.3 * text_score`, because that expression is
not a distance and no index describes its ordering. Adding the index without
restructuring the query would have produced a pleasing migration, a slower
database, and no change whatsoever to query time.

So this migration is the half that makes the other half possible: the query in
async_file_repository.hybrid_search is rewritten at the same time into two
indexed searches whose results are fused, which is the shape both indexes can
actually serve.

segments.tsv is a stored generated column rather than an expression index
because the query needs the vector itself for ranking, not only for matching.

Revision ID: 20260804_retrieval_idx
Revises: 20260803_invite_rs
"""
from alembic import op


revision = "20260804_retrieval_idx"
down_revision = "20260803_invite_rs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The text vector, computed once at write time instead of on every read.
    # 'english' matches what hybrid_search queries with; a mismatch here would
    # silently return nothing, because a tsquery built with one configuration
    # does not match a tsvector built with another.
    op.execute(
        """
        ALTER TABLE segments
        ADD COLUMN IF NOT EXISTS tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_segments_tsv ON segments USING gin (tsv)")

    # Cosine, because hybrid_search uses the <=> operator. An index built for
    # L2 would be ignored by a cosine query rather than being wrong, which is
    # the kind of failure that looks like "the index did not help".
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw
        ON chunks USING hnsw (embedding vector_cosine_ops)
        """
    )

    # Retrieval is scoped by workspace, so it always joins chunks and segments
    # back to files. Postgres does not index foreign keys for you.
    op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_file_id ON chunks (file_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_segment_id ON chunks (segment_id)")
    # read_page looks up one page of one document and had nothing to use.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_segments_file_page ON segments (file_id, page_number)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_segments_file_page")
    op.execute("DROP INDEX IF EXISTS ix_chunks_segment_id")
    op.execute("DROP INDEX IF EXISTS ix_chunks_file_id")
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_segments_tsv")
    op.execute("ALTER TABLE segments DROP COLUMN IF EXISTS tsv")
