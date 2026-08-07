"""Give chunks their own text, so retrieval can be finer than a citation.

WHY

A retrieval unit and a citation unit were the same object, and that object was
a page. Not by decision: chunk_text targets 200 tokens and multiplies by four
for PDFs, so the splitter fires at 800 tokens against pages averaging about
520, and almost never fires at all. A chunk became a page by arithmetic.

Measured consequence, on 27 benchmark questions with page-sized chunks:

    every required source in the fused top 25    24/27
    benchmark score                              18/27

Recall is not the problem. Rank and density are. A single-document gold page
lands at rank 1 to 8; the second source of a multi-document question lands at
12, 14, 22, 24, buried inside twenty-five whole pages of prose, of which the
two paragraphs that matter are a small fraction. Every experiment on this model
has shown it degrading as irrelevant context grows, three times in three.

WHAT CHANGES

    segments   one per page. The citation unit, because a page is what a
               reader opens. Keeps the full page text.
    chunks     several per page. The retrieval unit: 400 tokens with overlap,
               its own embedding, and now its own text and tsvector.

chunks.segment_id already existed and already pointed at the page, so the
hierarchy was in the schema and unused. Only the text was missing.

Nothing is backfilled. Existing chunks keep a null content and retrieval falls
back to the segment's text for them, so documents uploaded before this keep
working exactly as they did until they are re-ingested.

Revision ID: 20260806_chunk_retrieval
Revises: 20260805_file_outline
"""
from alembic import op


revision = "20260806_chunk_retrieval"
down_revision = "20260805_file_outline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS content text")

    # Same weighting as segments.tsv: the context sentence written at ingestion
    # counts for more than the body it describes.
    op.execute(
        """
        ALTER TABLE chunks
        ADD COLUMN IF NOT EXISTS tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_tsv ON chunks USING gin (tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_tsv")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS tsv")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS content")
